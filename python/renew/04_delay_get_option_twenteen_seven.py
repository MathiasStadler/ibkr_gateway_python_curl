#!/usr/bin/env python3
"""Fetch delayed option contract data via IBKR Gateway REST API and write to CSV.

04_delay_get_option_twenteen_six.py
------------------------------------
Robust version – collects puts, snapshots market fields, corrects Greek
signs for puts, and writes results to CSV immediately (even with missing
fields).
"""

from __future__ import annotations

import csv
import json
import logging
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
from requests.adapters import HTTPAdapter
import urllib3

# ----------------------------------------------------------------------
# Konfiguration
# ----------------------------------------------------------------------
IBKR_HOST = "localhost"
IBKR_PORT = 4002
IBKR_BASE_PATH = "/v1/api/iserver"
IBKR_TIMEOUT = 10
IBKR_MAX_RETRIES = 3
RETRY_BASE_DELAY = 2.0
RETRY_MAX_DELAY = 30.0
SNAPSHOT_MAX_ATTEMPTS = 3
FILL_RATE_THRESHOLD = 0.8  # 80 % contracts must have a field before skipping retry


@dataclass(frozen=True)
class Config:
    ibkr_host: str = IBKR_HOST
    ibkr_port: int = IBKR_PORT
    ibkr_base_path: str = IBKR_BASE_PATH
    verify_ssl: bool = False
    request_timeout: int = IBKR_TIMEOUT
    max_retries: int = IBKR_MAX_RETRIES
    retry_base_delay: float = RETRY_BASE_DELAY
    retry_max_delay: float = RETRY_MAX_DELAY
    batch_size: int = 10
    batch_delay: float = 1.5
    preferred_exchanges: Tuple[str, ...] = (
        "NASDAQ",
        "NYSE",
        "NYSE MKT",
        "BATS",
        "SMART",
        "AMEX",
    )
    filter_delta: bool = False
    force_put_only: bool = True
    delta_min: float = -0.50
    delta_max: float = -0.30
    log_level: int = logging.INFO
    log_format: str = "%(asctime)s - %(levelname)s : %(lineno)d - %(message)s"
    csv_output: str = "./DelayOptionContracts.csv"
    debug_log: str = "./option_debug.log"
    stock_price_csv: str = "./stock_price.csv"

    @property
    def base_url(self) -> str:
        return f"https://{self.ibkr_host}:{self.ibkr_port}{self.ibkr_base_path}"

    @classmethod
    def from_env(cls) -> Config:
        return cls(
            ibkr_host=__import__("os").getenv("IBKR_HOST", IBKR_HOST),
            ibkr_port=int(__import__("os").getenv("IBKR_PORT", str(IBKR_PORT))),
            verify_ssl=__import__("os").getenv("IBKR_VERIFY_SSL", "false").lower() == "true",
            request_timeout=int(__import__("os").getenv("IBKR_TIMEOUT", str(IBKR_TIMEOUT))),
            max_retries=int(__import__("os").getenv("IBKR_MAX_RETRIES", str(IBKR_MAX_RETRIES))),
            log_level=getattr(
                logging,
                __import__("os").getenv("LOG_LEVEL", "INFO").upper(),
                logging.INFO,
            ),
        )


# ----------------------------------------------------------------------
# Result Type
# ----------------------------------------------------------------------
@dataclass
class Result:
    ok: bool
    data: Any = None
    error: Optional[str] = None

    @classmethod
    def success(cls, data: Any) -> Result:
        return cls(ok=True, data=data)

    @classmethod
    def failure(cls, error: str) -> Result:
        return cls(ok=False, error=error)

    def unwrap(self) -> Any:
        if not self.ok:
            raise RuntimeError(f"Result is error: {self.error}")
        return self.data


# ----------------------------------------------------------------------
# Domain Models
# ----------------------------------------------------------------------
@dataclass
class StockPrice:
    symbol: str
    conid: int
    last: float
    bid: Optional[float] = None
    ask: Optional[float] = None
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class OptionContract:
    conid: int
    symbol: str
    strike: float
    maturity_date: str
    right: str = "P"
    bid: Optional[float] = None
    ask: Optional[float] = None
    delta: Optional[float] = None
    gamma: Optional[float] = None
    theta: Optional[float] = None
    vega: Optional[float] = None
    volume: Optional[int] = None
    open_interest: Optional[int] = None
    historical_volatility: Optional[float] = None
    implied_volatility: Optional[float] = None

    def to_csv_row(self) -> Dict[str, Any]:
        return {k: (v if v is not None else "") for k, v in asdict(self).items()}


@dataclass
class SecdefSearchResult:
    under_conid: int
    months: List[str]


# ----------------------------------------------------------------------
# Logging Setup
# ----------------------------------------------------------------------
def setup_logging(config: Config) -> logging.Logger:
    logging.basicConfig(level=config.log_level, format=config.log_format)
    logger = logging.getLogger(__name__)
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    return logger


# ----------------------------------------------------------------------
# Retry Decorator
# ----------------------------------------------------------------------
def with_retry(
    max_attempts: int = IBKR_MAX_RETRIES,
    base_delay: float = RETRY_BASE_DELAY,
    max_delay: float = RETRY_MAX_DELAY,
    exceptions: Tuple[type[BaseException], ...] = (Exception,),
):
    def decorator(func: Any) -> Any:
        from functools import wraps

        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            last_exc: Optional[BaseException] = None
            for attempt in range(max_attempts):
                try:
                    return func(*args, **kwargs)
                except exceptions as exc:
                    last_exc = exc
                    if attempt < max_attempts - 1:
                        delay = min(base_delay * (2**attempt), max_delay)
                        logging.warning(
                            f"Attempt {attempt + 1}/{max_attempts} failed for {func.__name__}: {exc}. "
                            f"Retrying in {delay:.1f}s..."
                        )
                        time.sleep(delay)
                    else:
                        logging.error(
                            f"All {max_attempts} attempts failed for {func.__name__}: {exc}"
                        )
            raise last_exc  # type: ignore[misc]

        return wrapper

    return decorator


# ----------------------------------------------------------------------
# IBKR Client
# ----------------------------------------------------------------------
FIELD_MAP: Dict[str, str] = {
    "31": "last",
    "84": "bid",
    "85": "ask",
    "86": "delta",
    "87": "gamma",
    "88": "theta",
    "89": "vega",
    "100": "volume",
    "101": "open_interest",
    "104": "historical_volatility",
    "106": "implied_volatility",
}

# Snapshot field IDs used for options – standard delayed fields only.
SNAPSHOT_FIELDS: Dict[str, str] = {
    "86": "delta",
    "88": "theta",
    "85": "volume",
    "84": "open_interest",
}

# Correct mapping for the "twentyeen_six" mode (delta + theta only).
BASIC_FIELDS: Dict[str, str] = {
    "86": "delta",
    "88": "theta",
}


class IBKRClient:
    def __init__(self, config: Config, logger: logging.Logger) -> None:
        self.config = config
        self.logger = logger
        self._session: Optional[requests.Session] = None
        self._authenticated = False

    @property
    def session(self) -> requests.Session:
        if self._session is None:
            retry_strategy = urllib3.util.retry.Retry(
                total=5,
                backoff_factor=1,
                status_forcelist=[429, 500, 502, 503, 504],
                allowed_methods=["GET", "POST"],
                respect_retry_after_header=True,
            )
            adapter = HTTPAdapter(
                max_retries=retry_strategy,
                pool_connections=20,
                pool_maxsize=20,
            )
            self._session = requests.Session()
            self._session.mount("https://", adapter)
            self._session.mount("http://", adapter)
            self._session.headers.update(
                {
                    "User-Agent": "ibkr-options-client/1.0",
                    "Accept": "application/json",
                    "Connection": "keep-alive",
                }
            )
            self.logger.debug(
                "Request session configured with retry and connection pooling"
            )
        return self._session

    def _url(self, endpoint: str) -> str:
        return f"{self.config.base_url}{endpoint}"

    def _get(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> Result:
        url = self._url(endpoint)
        try:
            resp = self.session.get(
                url,
                params=params,
                verify=self.config.verify_ssl,
                timeout=self.config.request_timeout,
            )
            resp.raise_for_status()
            return Result.success(resp.json())
        except requests.RequestException as exc:
            return Result.failure(f"Request failed: {exc}")
        except json.JSONDecodeError as exc:
            return Result.failure(f"Invalid JSON response: {exc}")

    def authenticate(self) -> Result:
        result = self._get("/accounts")
        if result.ok:
            self.logger.info("✅ Market data session initialized.")
            self._authenticated = True
        else:
            self.logger.error(f"❌ Authentication failed: {result.error}")
        return result

    @with_retry(max_attempts=3, base_delay=2.0)
    def search_secdef(self, symbol: str) -> Result:
        result = self._get(f"/secdef/search?symbol={symbol}")
        if not result.ok:
            return result

        data = result.data
        if not isinstance(data, list):
            return Result.failure(f"Unexpected response type: {type(data)}")

        # Prefer preferred exchanges; fall back to first OPT section found.
        selected: Optional[Dict[str, Any]] = None
        for contract in data:
            if not isinstance(contract, dict):
                continue
            desc = contract.get("description", "")
            if desc in self.config.preferred_exchanges:
                for sec in contract.get("sections", []):
                    if sec.get("secType") == "OPT":
                        selected = contract
                        self.logger.info(f"Selected exchange: {desc}")
                        break
                if selected:
                    break

        if selected is None:
            for contract in data:
                if not isinstance(contract, dict):
                    continue
                for sec in contract.get("sections", []):
                    if sec.get("secType") == "OPT":
                        selected = contract
                        self.logger.info(
                            f"Fallback exchange: {contract.get('description', 'Unknown')}"
                        )
                        break
                if selected:
                    break

        if selected is None:
            return Result.failure(f"No option contract found for {symbol}")

        under_conid = selected.get("conid")
        if not under_conid:
            return Result.failure(f"No conid for {symbol}")

        months: List[str] = []
        for sec in selected.get("sections", []):
            if sec.get("secType") == "OPT":
                months_str = sec.get("months", "")
                if months_str:
                    months = months_str.split(";")
                break

        if not months:
            return Result.failure(f"No option months for {symbol}")
        return Result.success(SecdefSearchResult(under_conid=under_conid, months=months))

    @with_retry(max_attempts=3, base_delay=2.0)
    def get_strikes(self, under_conid: int, month: str, exchange: str = "SMART") -> Result:
        result = self._get(
            f"/secdef/strikes?conid={under_conid}&secType=OPT&month={month}&exchange={exchange}"
        )
        if not result.ok:
            return result
        strikes = result.data.get("put", [])
        self.logger.info(f"Month {month}: {len(strikes)} Put strikes")
        return Result.success(strikes)

    @with_retry(max_attempts=3, base_delay=2.0)
    def get_contract_info(
        self,
        conid: int,
        month: str,
        strike: float,
        right: str = "P",
        exchange: str = "SMART",
    ) -> Result:
        result = self._get(
            f"/secdef/info?conid={conid}&month={month}&strike={strike}&secType=OPT&right={right}&exchange={exchange}"
        )
        if not result.ok:
            return result

        contracts = [
            OptionContract(
                conid=c["conid"],
                symbol=c["symbol"],
                strike=c["strike"],
                maturity_date=c.get("maturityDate", ""),
                right=c.get("right", right),
            )
            for c in result.data
            if c.get("strike") == strike
        ]
        return Result.success(contracts)

    def _snapshot_single_field(
        self, conids: List[int], field: str
    ) -> Result:
        """Fetch a single market-data field for the given conids."""
        if not conids:
            return Result.success({})

        endpoint = f"/marketdata/snapshot?conids={','.join(map(str, conids))}&fields={field}&snapshot=0"

        for attempt in range(SNAPSHOT_MAX_ATTEMPTS):
            try:
                resp = self.session.get(
                    f"{self.config.base_url}{endpoint}",
                    verify=self.config.verify_ssl,
                    timeout=self.config.request_timeout,
                )
                resp.raise_for_status()
                return Result.success(resp.json())
            except Exception as exc:
                if attempt < SNAPSHOT_MAX_ATTEMPTS - 1:
                    delay = 2 ** (attempt + 1)
                    self.logger.warning(
                        f"Snapshot field {field} attempt {attempt + 1}/{SNAPSHOT_MAX_ATTEMPTS} failed: {exc}. "
                        f"Retrying in {delay}s..."
                    )
                    time.sleep(delay)
                else:
                    return Result.failure(
                        f"Snapshot field {field} failed after {SNAPSHOT_MAX_ATTEMPTS} attempts: {exc}"
                    )
        return Result.failure("Unexpected exit from snapshot retry loop")

    def _snapshot_fields(self, conids: List[int], field_ids: List[str]) -> Result:
        """Fetch multiple fields for the given conids in one pass per field."""
        if not conids:
            return Result.success({})

        merged: Dict[int, Dict[str, Any]] = {cid: {"conid": cid} for cid in conids}

        for field_id in field_ids:
            result = self._snapshot_single_field(conids, field_id)
            if not result.ok:
                self.logger.warning(f"Failed to fetch field {field_id}: {result.error}")
                continue

            data = result.data
            items = data if isinstance(data, list) else [data]

            for item in items:
                if not isinstance(item, dict):
                    continue
                cid = item.get("conid")
                if cid not in merged:
                    continue
                val = item.get(field_id)
                if val is None:
                    continue
                attr_name = FIELD_MAP.get(field_id)
                if attr_name and val not in ("", None):
                    merged[cid][attr_name] = val

        return Result.success(list(merged.values()))

    def get_stock_price(self, conid: int, symbol: str) -> Result:
        """Fetch last, bid, and ask for the underlying.

        Prioritises field '31' (last price) and falls back to the bid/ask
        midpoint if last is missing after retries.
        """
        auth = self.authenticate()
        if not auth.ok:
            return Result.failure(f"Auth failed: {auth.error}")

        endpoint = "/marketdata/snapshot"
        params: Dict[str, Any] = {
            "conids": conid,
            "fields": "31,84,85",
            "snapshot": "0",
        }

        for attempt in range(self.config.max_retries):
            try:
                resp = self.session.get(
                    f"{self.config.base_url}{endpoint}",
                    params=params,
                    verify=self.config.verify_ssl,
                    timeout=self.config.request_timeout,
                )
                resp.raise_for_status()
                data = resp.json()
            except requests.RequestException as exc:
                self.logger.error(f"Request error: {exc}")
                if attempt < self.config.max_retries - 1:
                    time.sleep(3)
                    continue
                return Result.failure(f"Request failed: {exc}")
            except json.JSONDecodeError as exc:
                self.logger.error(f"JSON decode error: {exc}")
                if attempt < self.config.max_retries - 1:
                    time.sleep(3)
                    continue
                return Result.failure(f"Invalid JSON: {exc}")
            except Exception as exc:
                self.logger.error(f"Unexpected error: {exc}")
                if attempt < self.config.max_retries - 1:
                    time.sleep(3)
                    continue
                return Result.failure(f"Error: {exc}")

        item = data[0] if isinstance(data, list) and len(data) > 0 else data
        if not isinstance(item, dict):
            return Result.failure(f"Unexpected response format: {type(item)}")

        last_val = item.get("31")
        if last_val is None or last_val == "":
            self.logger.warning(
                f"Last price (field 31) missing, attempt {attempt + 1}/{self.config.max_retries}"
            )
            if attempt < self.config.max_retries - 1:
                time.sleep(3)
                continue
            # Fallback: use bid/ask midpoint
            try:
                bid = float(item.get("84", 0))
                ask = float(item.get("85", 0))
                if bid > 0 and ask > 0:
                    mid = (bid + ask) / 2.0
                    self.logger.info(f"Using bid/ask midpoint as last: {mid}")
                    return Result.success(
                        StockPrice(symbol=symbol, conid=conid, last=mid, bid=bid, ask=ask)
                    )
            except (ValueError, TypeError):
                pass
            return Result.failure("Missing last price after all retries")

        numeric = re.compile(r"^-?\d+(\.\d+)?$")
        missing: List[str] = []
        invalid: List[str] = []
        for fid, fname in {"31": "last_price", "84": "bid", "85": "ask"}.items():
            val = item.get(fid)
            if val is None or val == "":
                missing.append(fname)
            elif not numeric.match(str(val)):
                invalid.append(fname)

        if missing or invalid:
            self.logger.warning(
                f"Missing: {missing}, Invalid: {invalid}, attempt {attempt + 1}/{self.config.max_retries}"
            )
            if attempt < self.config.max_retries - 1:
                time.sleep(3)
                continue
            try:
                bid = float(item["84"]) if item.get("84") else None
                ask = float(item["85"]) if item.get("85") else None
                if bid and ask:
                    mid = (bid + ask) / 2.0
                    self.logger.info(f"Using bid/ask midpoint as last: {mid}")
                    return Result.success(
                        StockPrice(symbol=symbol, conid=conid, last=mid, bid=bid, ask=ask)
                    )
            except (ValueError, TypeError):
                pass
            return Result.failure(f"Missing: {missing}, Invalid: {invalid}")

        last = float(item["31"])
        bid = float(item["84"]) if item.get("84") else None
        ask = float(item["85"]) if item.get("85") else None
        return Result.success(
            StockPrice(symbol=symbol, conid=conid, last=last, bid=bid, ask=ask)
        )

    def close(self) -> None:
        """Close the underlying HTTP session to free resources."""
        if self._session is not None:
            self._session.close()
            self._session = None
            self.logger.debug("IBKR client session closed")


# ----------------------------------------------------------------------
# Helper Functions
# ----------------------------------------------------------------------
def _collect_contracts_for_month(
    client: IBKRClient,
    under_conid: int,
    month: str,
    max_per_month: int,
    current_price: float,
    logger: logging.Logger,
) -> List[OptionContract]:
    """Collect put option contracts for a single expiry month."""
    strikes_result = client.get_strikes(under_conid, month)
    if not strikes_result.ok:
        logger.warning(f"Failed to get strikes for month {month}: {strikes_result.error}")
        return []

    strikes = strikes_result.data
    if not strikes:
        logger.warning(f"No strikes found for month {month}")
        return []

    # Keep OTM/ATM puts only (strike <= current price), descending.
    valid = sorted([s for s in strikes if s <= current_price], reverse=True)
    if not valid:
        logger.warning(f"No strikes <= current price ({current_price}) for month {month}")
        return []

    selected = valid[:max_per_month]
    logger.info(f"Month {month}: Selected strikes {selected}")

    contracts: List[OptionContract] = []
    for strike in selected:
        info = client.get_contract_info(under_conid, month, strike, "P")
        if not info.ok:
            logger.warning(
                f"Failed to get contract info for {under_conid} {month} {strike}P: {info.error}"
            )
            continue
        month_contracts = info.data
        if not month_contracts:
            logger.warning(f"No contracts returned for {under_conid} {month} {strike}P")
            continue
        contracts.extend(month_contracts)
        logger.info(f"Month {month}: Collected {len(month_contracts)} contracts for strike {strike}")

    return contracts


def collect_contracts(
    client: IBKRClient,
    under_conid: int,
    months: List[str],
    current_price: float,
    max_per_month: int,
    logger: logging.Logger,
) -> List[OptionContract]:
    """Collect put option contracts across multiple expiry months."""
    all_contracts: List[OptionContract] = []
    for month in months:
        all_contracts.extend(
            _collect_contracts_for_month(client, under_conid, month, max_per_month, current_price, logger)
        )
    return all_contracts


def append_stock_price_csv(stock: StockPrice, csv_path: str, logger: logging.Logger) -> Result:
    """Append a single stock-price row to CSV (creates file with header if absent)."""
    try:
        path = Path(csv_path)
        file_exists = path.is_file()
        with open(path, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=asdict(stock).keys())
            if not file_exists:
                writer.writeheader()
            writer.writerow(asdict(stock))
        logger.info(f"Stock price appended to {csv_path}")
        return Result.success(True)
    except Exception as exc:
        logger.error(f"Failed to append stock price to CSV: {exc}")
        return Result.failure(f"Failed to append stock price to CSV: {exc}")


def write_debug_log(
    contracts: List[OptionContract], log_path: str, logger: logging.Logger
) -> Result:
    """Write all contracts as JSON-lines for debugging."""
    try:
        with open(log_path, "w") as f:
            f.write(f"# Debug log created at {datetime.now().isoformat()}\n")
            f.write(f"# Total contracts: {len(contracts)}\n")
            for c in contracts:
                f.write(json.dumps(asdict(c)) + "\n")
        logger.info(f"Debug log written to {log_path}")
        return Result.success(True)
    except Exception as exc:
        logger.error(f"Failed to write debug log: {exc}")
        return Result.failure(f"Failed to write debug log: {exc}")


HEADER_ORDER = [
    "conid",
    "symbol",
    "right",
    "strike",
    "maturity_date",
    "bid",
    "ask",
    "delta",
    "gamma",
    "theta",
    "vega",
    "volume",
    "open_interest",
    "historical_volatility",
    "implied_volatility",
]


def write_csv(
    contracts: List[OptionContract], csv_path: str, logger: logging.Logger
) -> Result:
    """Write contracts to CSV with a fixed header order."""
    try:
        path = Path(csv_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        with open(path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=HEADER_ORDER)
            writer.writeheader()
            for c in contracts:
                writer.writerow(c.to_csv_row())

        logger.info(f"Options CSV saved to {csv_path} ({len(contracts)} rows)")
        return Result.success(True)
    except Exception as exc:
        logger.error(f"Failed to write CSV: {exc}")
        return Result.failure(f"Failed to write CSV: {exc}")


def correct_put_greeks(contract: OptionContract) -> None:
    """Correct Greek signs for put options (IBKR returns inverted signs for puts)."""
    if contract.right != "P":
        return
    # Delta for puts is negative in IBKR native format; ensure it stays negative.
    if contract.delta is not None:
        contract.delta = -abs(contract.delta)
    # Gamma, vega are positive for both calls and puts in IBKR.
    if contract.gamma is not None:
        contract.gamma = abs(contract.gamma)
    if contract.vega is not None:
        contract.vega = abs(contract.vega)
    # Theta is negative (time decay); ensure it stays negative.
    if contract.theta is not None:
        contract.theta = -abs(contract.theta)


def is_numeric(val: Any) -> bool:
    """Return True if val can be interpreted as a number."""
    if val is None:
        return False
    if isinstance(val, (int, float)):
        return True
    try:
        float(val)
        return True
    except (ValueError, TypeError):
        return False


def has_missing_fields(contract: OptionContract, required: set[str]) -> bool:
    """Check whether any required attribute is None or non-numeric."""
    for attr in required:
        val = getattr(contract, attr, None)
        if val is None or not is_numeric(val):
            return True
    return False


def fetch_missing_fields(
    client: IBKRClient,
    conids: List[int],
    field_ids: List[str],
    missing_contracts: List[OptionContract],
    merged: Dict[int, Dict[str, Any]],
    logger: logging.Logger,
) -> None:
    """Retry fetching only the fields that are still missing for the given contracts."""
    for field_id in field_ids:
        # Does any missing contract still need this field?
        needs_retry = any(
            getattr(c, SNAPSHOT_FIELDS.get(field_id, field_id), None) is None
            or not is_numeric(getattr(c, SNAPSHOT_FIELDS.get(field_id, field_id), None))
            for c in missing_contracts
        )
        if not needs_retry:
            continue

        logger.info(f"Re-fetching field {field_id} for {len(missing_contracts)} contracts...")
        for attempt in range(SNAPSHOT_MAX_ATTEMPTS):
            result = client._snapshot_single_field([c.conid for c in missing_contracts], field_id)
            if not result.ok:
                delay = 2 ** (attempt + 1)
                logger.warning(
                    f"Re-fetch attempt {attempt + 1}/{SNAPSHOT_MAX_ATTEMPTS} for field {field_id}: {result.error}. "
                    f"Waiting {delay}s..."
                )
                time.sleep(delay)
                continue
            data = result.data
            if not isinstance(data, list):
                data = [data]
            for item in data:
                if not isinstance(item, dict):
                    continue
                cid = item.get("conid")
                if cid not in merged:
                    continue
                val = item.get(field_id)
                if val is not None:
                    try:
                        val = float(val)
                    except (ValueError, TypeError):
                        pass
                    merged[cid][SNAPSHOT_FIELDS.get(field_id, field_id)] = val
            break


def attach_snapshot_data(
    client: IBKRClient,
    contracts: List[OptionContract],
    field_map: Dict[str, str],
    logger: logging.Logger,
) -> None:
    """Fetch market snapshot fields and attach values to contracts in-place."""
    conids = [c.conid for c in contracts]
    field_ids = list(field_map.keys())

    merged: Dict[int, Dict[str, Any]] = {cid: {"conid": cid} for cid in conids}

    for field_id in field_ids:
        attr_name = field_map[field_id]
        logger.info(f"Fetching field {field_id} ({attr_name})...")

        for attempt in range(SNAPSHOT_MAX_ATTEMPTS):
            result = client._snapshot_single_field(conids, field_id)
            if not result.ok:
                delay = 2 ** (attempt + 1)
                logger.warning(
                    f"Attempt {attempt + 1}/{SNAPSHOT_MAX_ATTEMPTS} for field {field_id}: {result.error}. "
                    f"Retrying in {delay}s..."
                )
                time.sleep(delay)
                continue

            data = result.data
            if not isinstance(data, list):
                data = [data]

            filled = 0
            for item in data:
                if not isinstance(item, dict):
                    continue
                cid = item.get("conid")
                if cid not in merged:
                    continue
                val = item.get(field_id)
                if val is None:
                    continue
                try:
                    val = float(val)
                except (ValueError, TypeError):
                    pass
                merged[cid][attr_name] = val
                filled += 1

            if filled >= len(conids) * FILL_RATE_THRESHOLD:
                logger.info(
                    f"Field {field_id} filled for {filled}/{len(conids)} contracts. Moving on."
                )
                break
            else:
                logger.warning(
                    f"Field {field_id} only filled for {filled}/{len(conids)}. "
                    f"Will retry after 2s pause..."
                )
                time.sleep(2)

    # Attach merged values to contract objects.
    for contract in contracts:
        attrs = merged.get(contract.conid)
        if attrs:
            for attr, value in attrs.items():
                if attr != "conid" and hasattr(contract, attr):
                    setattr(contract, attr, value)

    # Second pass: retry any contracts that are still missing fields.
    required = set(field_map.values())
    still_missing = [c for c in contracts if has_missing_fields(c, required)]
    if still_missing:
        logger.info(f"{len(still_missing)} contracts still have missing fields after initial fetch. Retrying...")
        time.sleep(2)
        fetch_missing_fields(client, conids, field_ids, still_missing, merged, logger)

    # Second pass: re-attach any values that were filled in the retry.
    for contract in still_missing:
        attrs = merged.get(contract.conid)
        if attrs:
            for attr, value in attrs.items():
                if attr != "conid" and hasattr(contract, attr):
                    setattr(contract, attr, value)


def parse_cli_args() -> Tuple[str, int, int]:
    """Parse and validate CLI arguments. Returns (ticker, num_months, max_per_month)."""
    if len(sys.argv) < 4:
        print(
            "Usage: python3 04_delay_get_option_twenteen_six.py <TICKER> <MONTHS> <MAX_PER_MONTH>"
        )
        print("Example: python3 04_delay_get_option_twenteen_six.py TREX 1 5")
        raise SystemExit(1)

    ticker = sys.argv[1].upper()
    try:
        num_months = int(sys.argv[2])
        max_per_month = int(sys.argv[3])
    except ValueError:
        print("Error: MONTHS and MAX_PER_MONTH must be integers")
        raise SystemExit(1)

    if num_months < 1:
        print("Error: MONTHS must be >= 1")
        raise SystemExit(1)
    if max_per_month < 1:
        print("Error: MAX_PER_MONTH must be >= 1")
        raise SystemExit(1)

    return ticker, num_months, max_per_month


def resolve_field_map(mode: str) -> Dict[str, str]:
    """Return the field-ID → attribute mapping for the given snapshot mode."""
    if mode == "basic":
        return dict(BASIC_FIELDS)
    if mode == "full":
        return dict(SNAPSHOT_FIELDS)
    return dict(BASIC_FIELDS)


# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------
def main() -> int:
    """Orchestrate the delayed option-data collection workflow."""
    ticker, num_months, max_per_month = parse_cli_args()

    config = Config.from_env()
    logger = setup_logging(config)
    client = IBKRClient(config, logger)
    
    try:
        logger.info(
            f"Processing ticker: {ticker}, months: {num_months}, max/month: {max_per_month}"
        )

        # 1) Resolve underlying conid and available expiry months.
        search = client.search_secdef(ticker)
        if not search.ok:
            logger.error(f"Secdef search failed: {search.error}")
            return 1

        under_conid = search.data.under_conid
        months = search.data.months[:num_months]
        logger.info(f"Underlying conid: {under_conid}, months: {months}")

        # 2) Fetch current stock price.
        price_result = client.get_stock_price(under_conid, ticker)
        if not price_result.ok:
            logger.error(f"Stock price lookup failed: {price_result.error}")
            return 1

        stock = price_result.data
        logger.info(f"Current price for {ticker}: {stock.last}")
        append_stock_price_csv(stock, config.stock_price_csv, logger)

        # 3) Collect put option contracts for each month.
        contracts = collect_contracts(
            client, under_conid, months, stock.last, max_per_month, logger
        )
        if not contracts:
            logger.warning("No contracts found. Writing empty CSV (header only).")
            write_csv([], config.csv_output, logger)
            return 0

        # 4) Sort by expiry date, keep only OTM/ATM (strike < price), take top 10.
        contracts.sort(key=lambda c: c.maturity_date)
        otm = [c for c in contracts if c.strike < stock.last]
        otm.sort(key=lambda c: c.strike, reverse=True)
        top_contracts = otm[:10]

        if not top_contracts:
            logger.warning("No contracts with strike < current price found.")
            write_csv([], config.csv_output, logger)
            return 0

        logger.info(f"Processing {len(top_contracts)} contracts for snapshot...")

        # 5) Snapshot market fields and attach to contracts.
        field_map = resolve_field_map("basic")
        attach_snapshot_data(client, top_contracts, field_map, logger)

        # 6) Correct Greek signs for puts.
        for c in top_contracts:
            correct_put_greeks(c)

        # 7) Persist outputs.
        write_debug_log(top_contracts, config.debug_log, logger)
        csv_result = write_csv(top_contracts, config.csv_output, logger)
        if not csv_result.ok:
            return 1

        logger.info("Script completed successfully.")
        return 0
    finally:
        # Ensure the IBKR client session is always closed to free resources
        client.close()


if __name__ == "__main__":
    sys.exit(main())