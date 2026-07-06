#!/usr/bin/env python3
# 04_delay_get_option_twenteen_six.py
# -------------------------------
# Robust Version – CSV writes immediately regardless of missing fields
# -------------------------------

from __future__ import annotations

import sys
import os
import csv
import json
import time
import logging
import urllib3
from datetime import datetime
from dataclasses import dataclass, field, asdict
from typing import Optional, Any, Dict, Tuple, List
from functools import wraps
from pathlib import Path
import re

import requests
from requests.adapters import HTTPAdapter


# ----------------------------------------------------------------------
# Konfiguration
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class Config:
    ibkr_host: str = "localhost"
    ibkr_port: int = 4002
    ibkr_base_path: str = "/v1/api/iserver"
    verify_ssl: bool = False
    request_timeout: int = 10
    max_retries: int = 3
    retry_base_delay: float = 2.0
    retry_max_delay: float = 30.0
    batch_size: int = 10
    batch_delay: float = 1.5
    preferred_exchanges: tuple[str, ...] = (
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
    def from_env(cls) -> "Config":
        return cls(
            ibkr_host=os.getenv("IBKR_HOST", "localhost"),
            ibkr_port=int(os.getenv("IBKR_PORT", "4002")),
            verify_ssl=os.getenv("IBKR_VERIFY_SSL", "false").lower() == "true",
            request_timeout=int(os.getenv("IBKR_TIMEOUT", "10")),
            max_retries=int(os.getenv("IBKR_MAX_RETRIES", "3")),
            log_level=getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO),
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
    def success(cls, data: Any) -> "Result":
        return cls(ok=True, data=data)

    @classmethod
    def failure(cls, error: str) -> "Result":
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
    max_attempts: int = 3,
    base_delay: float = 2.0,
    max_delay: float = 30.0,
    exceptions: Tuple[type[BaseException], ...] = (Exception,),
):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_exc = None
            for attempt in range(max_attempts):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exc = e
                    if attempt < max_attempts - 1:
                        delay = min(base_delay * (2 ** attempt), max_delay)
                        logging.warning(
                            f"Attempt {attempt + 1}/{max_attempts} failed for {func.__name__}: {e}. "
                            f"Retrying in {delay:.1f}s..."
                        )
                        time.sleep(delay)
                    else:
                        logging.error(f"All {max_attempts} attempts failed for {func.__name__}: {e}")
            raise last_exc
        return wrapper
    return decorator


# ----------------------------------------------------------------------
# IBKR Client
# ----------------------------------------------------------------------
class IBKRClient:
    FIELD_MAP = {
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

    def __init__(self, config: Config, logger: logging.Logger):
        self.config = config
        self.logger = logger
        self._session: Optional[requests.Session] = None

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
            self.logger.debug("Request session configured with enhanced retry and connection pooling")
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
        except requests.RequestException as e:
            return Result.failure(f"Request failed: {e}")
        except json.JSONDecodeError as e:
            return Result.failure(f"Invalid JSON response: {e}")

    def authenticate(self) -> Result:
        result = self._get("/accounts")
        if result.ok:
            self.logger.info("✅ Market data session initialized.")
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
        selected = None
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
        if not selected:
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
        if not selected:
            return Result.failure(f"No option contract found for {symbol}")
        under_conid = selected.get("conid")
        if not under_conid:
            return Result.failure(f"No conid for {symbol}")
        months = []
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
        contracts = []
        for c in result.data:
            if c.get("strike") == strike:
                contracts.append(
                    OptionContract(
                        conid=c["conid"],
                        symbol=c["symbol"],
                        strike=c["strike"],
                        maturity_date=c.get("maturityDate", ""),
                        right=c.get("right", right),
                    )
                )
        return Result.success(contracts)

    def _snapshot_single_field(self, conids: List[int], field: str) -> Result:
        "Fetches a single field for the given conids."
        endpoint = f"/marketdata/snapshot?conids={','.join(map(str, conids))}&fields={field}&snapshot=0"
        if not conids:
            return Result.success({})
        auth = self.authenticate()
        if not auth.ok:
            return Result.failure(f"Auth failed: {auth.error}")
        for attempt in range(3):
            try:
                resp = self.session.get(
                    f"{self.config.base_url}{endpoint}",
                    verify=self.config.verify_ssl,
                    timeout=self.config.request_timeout,
                )
                resp.raise_for_status()
                data = resp.json()
                return Result.success(data)
            except Exception as e:
                if attempt < 2:
                    self.logger.warning(
                        f"Request failed (attempt {attempt + 1}/3): {e}. Retrying in 3s..."
                    )
                    time.sleep(3)
                else:
                    return Result.failure(f"Request failed after 3 attempts: {e}")
        return Result.failure("Unexpected exit from retry loop")

    def _snapshot_raw(self, conids: List[int], fields: str) -> Result:
        "Fetches multiple fields for the given conids."
        if not conids:
            return Result.success({})

        field_list = [f.strip() for f in fields.split(",") if f.strip()]

        merged: Dict[int, Dict[str, Any]] = {cid: {"conid": cid} for cid in conids}
        for field_id in field_list:
            result = self._snapshot_single_field(conids, field_id)
            if not result.ok:
                self.logger.warning(f"Failed to fetch field {field_id}: {result.error}")
                continue
            data = result.data
            items = data if isinstance(data, list) else [data]
            for item in items:
                if isinstance(item, dict):
                    cid = item.get("conid")
                    if cid in merged:
                        val = item.get(field_id)
                        if val is not None:
                            attr_name = self.FIELD_MAP.get(field_id)
                            if attr_name:
                                merged[cid][attr_name] = val
        return Result.success(list(merged.values()))

    def get_stock_price(self, conid: int, symbol: str) -> Result:
        "Enhanced version that prioritises the 'last' price field and retries specifically for missing '31' field."
        auth = self.authenticate()
        if not auth.ok:
            return Result.failure(f"Auth failed: {auth.error}")

        endpoint = "/marketdata/snapshot"
        params = {"conids": conid, "fields": "31,84,86", "snapshot": "0"}

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
            except requests.RequestException as e:
                self.logger.error(f"Request error: {e}")
                if attempt < self.config.max_retries - 1:
                    time.sleep(3)
                    continue
                else:
                    return Result.failure(f"Request failed: {e}")
            except json.JSONDecodeError as e:
                self.logger.error(f"JSON decode error: {e}")
                if attempt < self.config.max_retries - 1:
                    time.sleep(3)
                    continue
                else:
                    return Result.failure(f"Invalid JSON: {e}")
            except Exception as e:
                self.logger.error(f"Unexpected error: {e}")
                if attempt < self.config.max_retries - 1:
                    time.sleep(3)
                    continue
                else:
                    return Result.failure(f"Error: {e}")

            item = data[0] if isinstance(data, list) and len(data) > 0 else data

            if not isinstance(item, dict):
                self.logger.error(f"Unexpected response format: {type(item)}")
                if attempt < self.config.max_retries - 1:
                    time.sleep(3)
                    continue
                else:
                    return Result.failure(f"Invalid response format: {type(item)}")

            last_val = item.get("31")
            if last_val is None or last_val == "":
                self.logger.warning(
                    f"Last price (field 31) is missing, attempt {attempt + 1}/{self.config.max_retries}"
                )
                if attempt < self.config.max_retries - 1:
                    time.sleep(3)
                    continue
                else:
                    bid_val = item.get("84")
                    ask_val = item.get("86")
                    if bid_val is not None and bid_val != "" and ask_val is not None and ask_val != "":
                        try:
                            bid = float(bid_val)
                            ask = float(ask_val)
                            last = (bid + ask) / 2.0
                            self.logger.info(f"Using bid/ask midpoint as last: {last}")
                            return Result.success(
                                StockPrice(symbol=symbol, conid=conid, last=last, bid=bid, ask=ask)
                            )
                        except (ValueError, TypeError):
                            pass
                    return Result.failure("Missing last price after all retries")

            numeric_pattern = re.compile(r"^-?\d+(\.\d+)?$")

            missing_fields = []
            invalid_fields = []
            for field_id, field_name in {"31": "last_price", "84": "bid", "86": "ask"}.items():
                val = item.get(field_id)
                if val is None or val == "":
                    missing_fields.append(field_name)
                elif not numeric_pattern.match(str(val)):
                    invalid_fields.append(field_name)

            if missing_fields or invalid_fields:
                self.logger.warning(
                    f"Missing fields: {missing_fields}, Invalid fields: {invalid_fields}, attempt {attempt + 1}/{self.config.max_retries}"
                )
                if attempt < self.config.max_retries - 1:
                    time.sleep(3)
                    continue
                bid = float(item.get("84")) if item.get("84") else None
                ask = float(item.get("86")) if item.get("86") else None
                if bid and ask:
                    last = (bid + ask) / 2.0
                    self.logger.info(f"Using bid/ask midpoint as last: {last}")
                    return Result.success(
                        StockPrice(symbol=symbol, conid=conid, last=last, bid=bid, ask=ask)
                    )
                return Result.failure(f"Missing: {missing_fields}, Invalid: {invalid_fields}")

            last = float(item["31"])
            bid = float(item["84"]) if item.get("84") else None
            ask = float(item["86"]) if item.get("86") else None
            return Result.success(
                StockPrice(symbol=symbol, conid=conid, last=last, bid=bid, ask=ask)
            )

        return Result.failure("Max retries exceeded - this should not be reached")


# ----------------------------------------------------------------------
# Helper Functions
# ----------------------------------------------------------------------
def collect_contracts(
    client: IBKRClient,
    under_conid: int,
    months: List[str],
    current_price: float,
    max_per_month: int,
    logger: logging.Logger,
) -> List[OptionContract]:
    "Collect option contracts for given months, filtering for ATM/OTM puts."
    contracts = []
    for month in months:
        strikes_result = client.get_strikes(under_conid, month)
        if not strikes_result.ok:
            logger.warning(f"Failed to get strikes for month {month}: {strikes_result.error}")
            continue
        strikes = strikes_result.data
        if not strikes:
            logger.warning(f"No strikes found for month {month}")
            continue

        filtered_strikes = [s for s in strikes if s <= current_price]
        if not filtered_strikes:
            logger.warning(
                f"No strikes <= current price ({current_price}) for month {month}"
            )
            continue

        filtered_strikes.sort(reverse=True)
        selected_strikes = filtered_strikes[:max_per_month]
        logger.info(f"Month {month}: Selected strikes {selected_strikes}")

        for strike in selected_strikes:
            contracts_result = client.get_contract_info(
                under_conid, month, strike, "P"
            )
            if not contracts_result.ok:
                logger.warning(
                    f"Failed to get contract info for {under_conid} {month} {strike}P: {contracts_result.error}"
                )
                continue
            month_contracts = contracts_result.data
            if not month_contracts:
                logger.warning(
                    f"No contracts returned for {under_conid} {month} {strike}P"
                )
                continue
            contracts.extend(month_contracts)
            logger.info(
                f"Month {month}: Collected {len(month_contracts)} contracts for strike {strike}"
            )
    return contracts


def append_stock_price(stock: StockPrice, csv_path: str, logger: logging.Logger) -> Result:
    "Append stock price data to CSV file."
    try:
        file_exists = Path(csv_path).is_file()
        with open(csv_path, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=asdict(stock).keys())
            if not file_exists:
                writer.writeheader()
            writer.writerow(asdict(stock))
        logger.info(f"Stock price appended to {csv_path}")
        return Result.success(True)
    except Exception as e:
        logger.error(f"Failed to append stock price to CSV: {e}")
        return Result.failure(f"Failed to append stock price to CSV: {e}")


def write_debug_log(
    contracts: List[OptionContract], log_path: str, logger: logging.Logger
) -> Result:
    "Write debug log of all contracts."
    try:
        with open(log_path, "w") as f:
            f.write(
                f"# Debug log created at {datetime.now().isoformat()}\n"
            )
            f.write(f"# Total contracts: {len(contracts)}\n")
            for c in contracts:
                f.write(json.dumps(asdict(c)) + "\n")
        logger.info(f"Debug log written to {log_path}")
        return Result.success(True)
    except Exception as e:
        logger.error(f"Failed to write debug log: {e}")
        return Result.failure(f"Failed to write debug log: {e}")


def write_csv(
    contracts: List[OptionContract], csv_path: str, logger: logging.Logger
) -> Result:
    "Write contracts to CSV file."
    try:
        if not contracts:
            with open(csv_path, "w", newline="") as f:
                writer = csv.DictWriter(
                    f,
                    fieldnames=[
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
                    ],
                )
                writer.writeheader()
            logger.info(f"Empty CSV written to {csv_path} (header only)")
            return Result.success(True)

        with open(csv_path, "w", newline="") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=[
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
                ],
            )
            writer.writeheader()
            for c in contracts:
                writer.writerow(c.to_csv_row())
        logger.info(f"Options CSV saved to {csv_path}")
        return Result.success(True)
    except Exception as e:
        logger.error(f"Failed to write CSV: {e}")
        return Result.failure(f"Failed to write CSV: {e}")


def correct_put_greeks(contract: OptionContract) -> None:
    "Correct Greek signs for put options (IBKR returns inverted signs for puts)."
    if contract.right == "P":
        if contract.delta is not None and contract.delta > 0:
            contract.delta = -abs(contract.delta)
        if contract.gamma is not None and contract.gamma < 0:
            contract.gamma = abs(contract.gamma)
        if contract.vega is not None and contract.vega < 0:
            contract.vega = abs(contract.vega)
        if contract.theta is not None and contract.theta > 0:
            contract.theta = -abs(contract.theta)


def main() -> int:
    "Main function – orchestrates the entire workflow."
    if len(sys.argv) < 4:
        print(
            "Usage: python3 04_delay_get_option_twenteen_six.py <TICKER> <MONTHS> <MAX_PER_MONTH>"
        )
        print("Example: python3 04_delay_get_option_twenteen_six.py TREX 1 5")
        return 1

    ticker = sys.argv[1].upper()
    try:
        num_months = int(sys.argv[2])
        max_per_month = int(sys.argv[3])
    except ValueError:
        print("Error: MONTHS and MAX_PER_MONTH must be integers")
        return 1

    config = Config.from_env()
    logger = setup_logging(config)
    client = IBKRClient(config, logger)

    logger.info(
        f"Processing ticker: {ticker}, months: {num_months}, max/month: {max_per_month}"
    )

    search_result = client.search_secdef(ticker)
    if not search_result.ok:
        logger.error(f"Secdef search failed: {search_result.error}")
        return 1

    under_conid = search_result.data.under_conid
    months = search_result.data.months[:num_months]
    logger.info(
        f"Underlying conid: {under_conid}, months: {months}"
    )

    stock_result = client.get_stock_price(under_conid, ticker)
    if not stock_result.ok:
        logger.error(f"Stock price failed: {stock_result.error}")
        return 1

    stock = stock_result.data
    logger.info(f"Current price: {stock.last}")
    append_stock_price(stock, config.stock_price_csv, logger)

    contracts = collect_contracts(
        client, under_conid, months, stock.last, max_per_month, logger
    )
    if not contracts:
        logger.warning("No contracts found. Writing empty CSV.")
        write_csv([], config.csv_output, logger)
        return 0

    contracts.sort(key=lambda c: c.maturity_date)
    filtered = [c for c in contracts if c.strike < stock.last]
    filtered.sort(key=lambda c: c.strike, reverse=True)
    top_contracts = filtered[:10]

    if not top_contracts:
        logger.warning("No contracts with strike < current price.")
        write_csv([], config.csv_output, logger)
        return 0

    logger.info(f"Total contracts collected: {len(top_contracts)}")

    conids = [c.conid for c in top_contracts]

    # Füge das Flag INCLUDE_ADVANCED_GREEKS hinzu
    INCLUDE_ADVANCED_GREEKS: bool = False  # Standard: delta(86) + theta(88) nur

    # Feldmapping mit dynamischer Auswahl basierend auf dem Flag
    FIELD_TO_ATTR = {
        "86": "delta",  # Delta
        "88": "theta",  # Theta
    }

    # Aktivieren Sie Vega(89) und Rho (z.B. 107) wenn das Flag aktiviert ist
    if INCLUDE_ADVANCED_GREEKS:
        FIELD_TO_ATTR.update({
            "89": "vega",
            "107": "rho"
        })

    field_ids = ",".join(FIELD_TO_ATTR.keys())

    merged: Dict[int, Dict[str, Any]] = {cid: {"conid": cid} for cid in conids}

    for field_id, attr_name in FIELD_TO_ATTR.items():
        logger.info(f"Fetching field {field_id} ({attr_name})...")

        for attempt in range(3):
            result = self._snapshot_single_field(conids, field_id)
            if not result.ok:
                wait_time = 2 ** (attempt + 1)  # Exponential backoff: 2, 4, 8 seconds
                logger.warning(
                    f"Attempt {attempt+1}/3: Field {field_id} snapshot failed: {result.error}. Retrying in {wait_time}s..."
                )
                time.sleep(wait_time)
                continue

            data = result.data
            if not isinstance(data, list):
                data = [data]

            for item in data:
                if isinstance(item, dict):
                    cid = item.get("conid")
                    if cid in merged:
                        value = item.get(field_id)
                        if value is not None:
                            merged[cid][attr_name] = value

            def is_numeric(val):
                if val is None:
                    return False
                if isinstance(val, (int, float)):
                    return True
                try:
                    float(val)
                    return True
                except (ValueError, TypeError):
                    return False

            filled = sum(
                1 for cid in conids if is_numeric(merged.get(cid, {}).get(attr_name))
            )
            if filled >= len(conids) * 0.8:
                logger.info(
                    f"Field {field_id} filled for {filled}/{len(conids)} contracts. Moving on."
                )
                break
            else:
                logger.warning(
                    f"Field {field_id} only filled for {filled}/{len(conids)}. Retrying in 2s..."
                )
                time.sleep(2)

    for c in top_contracts:
        attrs = merged.get(c.conid)
        if attrs:
            for attr, value in attrs.items():
                if attr != "conid" and hasattr(c, attr):
                    setattr(c, attr, value)

    for c in top_contracts:
        correct_put_greeks(c)

    write_debug_log(top_contracts, config.debug_log, logger)
    csv_result = write_csv(top_contracts, config.csv_output, logger)
    if not csv_result.ok:
        return 1

    logger.info("Script completed successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())