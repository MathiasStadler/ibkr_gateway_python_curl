#!/usr/bin/env python3
"""Fetch delayed option contract data via IBKR Gateway REST API and write to CSV.

04_delay_get_option_twenteen_six.py
------------------------------------
Robust version – collects puts, snapshots market fields, corrects Greek
signs for puts, and writes results to CSV immediately (even with missing
fields). All public methods return Result objects. Supports polling for
market data to fill in delayed-mode fields over time.
"""

from __future__ import annotations

import argparse
import csv
import dataclasses
import json
import logging
import os
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
    preferred_exchanges: Tuple[str, ...] = (
        "NASDAQ", "NYSE", "NYSE MKT", "BATS", "SMART", "AMEX",
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
    poll_minutes: int = 3
    poll_interval: int = 30

    @property
    def base_url(self) -> str:
        return f"https://{self.ibkr_host}:{self.ibkr_port}{self.ibkr_base_path}"

    @classmethod
    def from_env(cls) -> Config:
        env = os.environ
        return cls(
            ibkr_host=env.get("IBKR_HOST", "localhost"),
            ibkr_port=int(env.get("IBKR_PORT", "4002")),
            verify_ssl=env.get("IBKR_VERIFY_SSL", "false").lower() in ("true", "1", "yes"),
            request_timeout=int(env.get("IBKR_TIMEOUT", "10")),
            max_retries=int(env.get("IBKR_MAX_RETRIES", "3")),
            log_level=getattr(logging, env.get("LOG_LEVEL", "INFO").upper(), logging.INFO),
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
    logger = logging.getLogger(__name__)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter(config.log_format))
        logger.addHandler(handler)
        logger.setLevel(config.log_level)
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    return logger


# ----------------------------------------------------------------------
# Retry Decorator & Helper Functions
# ----------------------------------------------------------------------
def with_retry(max_attempts: int = 3, base_delay: float = 2.0, max_delay: float = 30.0):
    def decorator(func: Any) -> Any:
        from functools import wraps
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            for attempt in range(max_attempts):
                try:
                    return func(*args, **kwargs)
                except Exception as exc:
                    if attempt < max_attempts - 1:
                        delay = min(base_delay * (2 ** attempt), max_delay)
                        logging.warning(
                            f"Attempt {attempt + 1}/{max_attempts} failed for {func.__name__}: {exc}. "
                            f"Retrying in {delay:.1f}s..."
                        )
                        time.sleep(delay)
                    else:
                        logging.error(f"All {max_attempts} attempts failed for {func.__name__}: {exc}")
                        raise
        return wrapper
    return decorator


# ----------------------------------------------------------------------
# IBKR Client
# ----------------------------------------------------------------------
FIELD_MAP: Dict[str, str] = {
    "31": "last", "84": "bid", "85": "ask", "86": "delta",
    "87": "gamma", "88": "theta", "89": "vega",
    "100": "volume", "101": "open_interest",
    "104": "historical_volatility", "106": "implied_volatility",
}

BASIC_FIELDS: List[str] = ["84", "85", "86", "87", "88", "89", "100", "101"]
SNAPSHOT_FIELDS: List[str] = ["31", "84", "85", "86", "87", "89", "100", "101", "104", "106"]
ALL_MARKET_FIELDS: List[str] = ["31", "84", "85", "86", "87", "88", "89", "100", "101", "104", "106", "900"]


class IBKRClient:
    def __init__(self, config: Config, logger: logging.Logger) -> None:
        self.config = config
        self.logger = logger
        self._session: Optional[requests.Session] = None

    @property
    def session(self) -> requests.Session:
        if self._session is None:
            retry_strategy = urllib3.util.retry.Retry(
                total=5, backoff_factor=1,
                status_forcelist=[429, 500, 502, 503, 504],
                allowed_methods=["GET", "POST"],
                respect_retry_after_header=True,
            )
            adapter = HTTPAdapter(max_retries=retry_strategy, pool_connections=20, pool_maxsize=20)
            self._session = requests.Session()
            self._session.mount("https://", adapter)
            self._session.mount("http://", adapter)
            self._session.headers.update({
                "User-Agent": "ibkr-options-client/1.0",
                "Accept": "application/json",
                "Connection": "keep-alive",
            })
        return self._session

    def _get(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> Result:
        try:
            resp = self.session.get(
                f"{self.config.base_url}{endpoint}",
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
        else:
            self.logger.error(f"❌ Authentication failed: {result.error}")
        return result

    def search_secdef(self, symbol: str) -> Result:
        result = self._get(f"/secdef/search?symbol={symbol}")
        if not result.ok:
            return result
        data = result.data
        if not isinstance(data, list):
            return Result.failure(f"Unexpected response type: {type(data)}")
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
        if not selected:
            for contract in data:
                if not isinstance(contract, dict):
                    continue
                for sec in contract.get("sections", []):
                    if sec.get("secType") == "OPT":
                        selected = contract
                        self.logger.info(f"Fallback exchange: {contract.get('description', 'Unknown')}")
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

    def get_strikes(self, under_conid: int, month: str) -> Result:
        result = self._get(f"/secdef/strikes?conid={under_conid}&secType=OPT&month={month}&exchange=SMART")
        if not result.ok:
            return result
        strikes = result.data.get("put", [])
        self.logger.info(f"Month {month}: {len(strikes)} Put strikes")
        return Result.success(strikes)

    def get_contract_info(self, under_conid: int, month: str, strike: float, right: str = "P", exchange: str = "SMART") -> Result:
        result = self._get(f"/secdef/info?conid={under_conid}&month={month}&strike={strike}&secType=OPT&right={right}&exchange={exchange}")
        if not result.ok:
            return result
        contracts = []
        for c in result.data:
            if isinstance(c, dict) and c.get("strike") == strike:
                contracts.append(OptionContract(
                    conid=c.get("conid", 0),
                    symbol=c.get("symbol", ""),
                    strike=c.get("strike", strike),
                    maturity_date=c.get("maturityDate", ""),
                    right=c.get("right", right),
                ))
        return Result.success(contracts)

    def _snapshot_fields(self, conids: List[int], field_ids: List[str], md_type: str = "1") -> Result:
        if not conids:
            return Result.success({})
        merged: Dict[int, Dict[str, Any]] = {cid: {"conid": cid} for cid in conids}
        fields_str = ",".join(field_ids) if isinstance(field_ids, list) else field_ids
        params: Dict[str, Any] = {
            "conids": ",".join(map(str, conids)),
            "fields": fields_str,
            "snapshot": "0",
            "mdType": md_type,
        }
        try:
            resp = self.session.get(f"{self.config.base_url}/marketdata/snapshot", params=params, verify=self.config.verify_ssl, timeout=self.config.request_timeout)
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as exc:
            return Result.failure(f"Snapshot request failed: {exc}")
        except json.JSONDecodeError as exc:
            return Result.failure(f"Invalid JSON response: {exc}")

        items = data if isinstance(data, list) else [data]
        for item in items:
            if not isinstance(item, dict):
                continue
            cid = item.get("conid")
            if cid not in merged:
                continue
            for field_id, attr_name in FIELD_MAP.items():
                val = item.get(field_id)
                if val is not None and val != "":
                    try:
                        if isinstance(val, str):
                            if "," in val and field_id not in ["84", "85"]:
                                val = val.replace(",", ".")
                        val = float(val)
                        attr_name_local = FIELD_MAP.get(field_id)
                        if attr_name_local:
                            merged[cid][attr_name_local] = val
                    except (ValueError, TypeError):
                        self.logger.debug(f"Could not convert field {field_id} value '{val}' to float")
        return Result.success(list(merged.values()))

    def get_stock_price(self, conid: int, symbol: str) -> Result:
        params: Dict[str, Any] = {
            "conids": conid,
            "fields": "31,84,85",
            "snapshot": "0",
            "mdType": "1",
        }
        for attempt in range(self.config.max_retries):
            try:
                resp = self.session.get(
                    f"{self.config.base_url}/marketdata/snapshot",
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

            item = data[0] if isinstance(data, list) and len(data) > 0 else data
            if not isinstance(item, dict):
                if attempt < self.config.max_retries - 1:
                    time.sleep(3)
                    continue
                return Result.failure(f"Unexpected response format: {type(item)}")

            last_val = item.get("31")
            if last_val is None or last_val == "":
                self.logger.warning(f"Last price (field 31) missing, attempt {attempt + 1}/{self.config.max_retries}")
                bid_val = None
                ask_val = None
                bid_raw = item.get("84")
                ask_raw = item.get("85")
                if bid_raw:
                    try:
                        bid_val = float(bid_raw.replace(",", "."))
                    except (ValueError, TypeError):
                        pass
                if ask_raw:
                    try:
                        ask_val = float(ask_raw.replace(",", "."))
                    except (ValueError, TypeError):
                        pass
                if bid_val and ask_val:
                    mid = (bid_val + ask_val) / 2.0
                    self.logger.info(f"Using bid/ask midpoint as last: {mid}")
                    return Result.success(StockPrice(symbol=symbol, conid=conid, last=mid, bid=bid_val, ask=ask_val))
                if attempt < self.config.max_retries - 1:
                    time.sleep(3)
                    continue
                return Result.failure("Missing last price after all retries")

            last = float(last_val)
            bid = None
            ask = None
            bid_raw = item.get("84")
            ask_raw = item.get("85")
            if bid_raw:
                try:
                    bid = float(str(bid_raw).replace(",", "."))
                except (ValueError, TypeError):
                    pass
            if ask_raw:
                try:
                    ask = float(str(ask_raw).replace(",", "."))
                except (ValueError, TypeError):
                    pass
            return Result.success(StockPrice(symbol=symbol, conid=conid, last=last, bid=bid, ask=ask))

        return Result.failure("Max retries exceeded in get_stock_price")

    def close(self) -> None:
        if self._session is not None:
            self._session.close()
            self._session = None
            self.logger.debug("IBKR client session closed")


# ----------------------------------------------------------------------
# Helper Functions
# ----------------------------------------------------------------------
def resolve_field_map(level: str) -> List[str]:
    if level == "basic":
        return BASIC_FIELDS
    elif level == "snapshot":
        return SNAPSHOT_FIELDS
    return BASIC_FIELDS


def correct_put_greeks(contract: OptionContract) -> None:
    if contract.right.upper() == "P":
        if contract.delta is not None:
            contract.delta = -contract.delta
        if contract.gamma is not None:
            contract.gamma = -contract.gamma
        if contract.theta is not None:
            contract.theta = -contract.theta
        if contract.vega is not None:
            contract.vega = -contract.vega


def collect_contracts(client: IBKRClient, under_conid: int, months: List[str], current_price: float, max_per_month: int, logger: logging.Logger) -> List[OptionContract]:
    all_contracts: List[OptionContract] = []
    for month in months:
        strikes_result = client.get_strikes(under_conid, month)
        if not strikes_result.ok:
            logger.warning(f"Failed to get strikes for month {month}: {strikes_result.error}")
            continue
        strikes = strikes_result.data
        if not strikes:
            continue
        valid = sorted([s for s in strikes if s <= current_price], reverse=True)
        selected = valid[:max_per_month]
        for strike in selected:
            info = client.get_contract_info(under_conid, month, strike, "P")
            if info.ok:
                all_contracts.extend(info.data)
            else:
                logger.warning(f"Failed to get contract info for {under_conid} {month} {strike}P: {info.error}")
    return all_contracts


def attach_snapshot_data(client: IBKRClient, contracts: List[OptionContract], field_ids: List[str], logger: logging.Logger, md_type: str = "1") -> None:
    if not contracts:
        return
    conids = [c.conid for c in contracts]
    result = client._snapshot_fields(conids, field_ids, md_type)
    if not result.ok:
        logger.warning(f"Failed to attach snapshot data: {result.error}")
        return
    items = result.data if isinstance(result.data, list) else [result.data]
    for item in items:
        if not isinstance(item, dict):
            continue
        cid = item.get("conid")
        if cid is None:
            continue
        for contract in contracts:
            if contract.conid == cid:
                for field_id, attr_name in FIELD_MAP.items():
                    val = item.get(field_id)
                    if val is not None:
                        try:
                            if isinstance(val, str):
                                val = float(val.replace(",", "."))
                            else:
                                val = float(val)
                            setattr(contract, attr_name, val)
                        except (ValueError, TypeError):
                            pass
                break


def append_stock_price_csv(stock: StockPrice, csv_path: str, logger: logging.Logger) -> Result:
    try:
        path = Path(csv_path)
        file_exists = path.is_file()
        with open(path, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(asdict(stock).keys()))
            if not file_exists:
                writer.writeheader()
            writer.writerow(asdict(stock))
        logger.info(f"Stock price appended to {csv_path}")
        return Result.success(True)
    except Exception as exc:
        logger.error(f"Failed to append stock price to CSV: {exc}")
        return Result.failure(f"Failed to append stock price to CSV: {exc}")


def write_csv(contracts: List[OptionContract], csv_path: str, logger: logging.Logger) -> Result:
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


def write_debug_log(contracts: List[OptionContract], log_path: str, logger: logging.Logger) -> Result:
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
    "conid", "symbol", "right", "strike", "maturity_date",
    "bid", "ask", "delta", "gamma", "theta", "vega",
    "volume", "open_interest", "historical_volatility", "implied_volatility",
]


def parse_cli_args() -> Tuple[str, int, int, int, int]:
    parser = argparse.ArgumentParser(description="Fetch delayed option contract data via IBKR Gateway REST API and write to CSV.")
    parser.add_argument("ticker", help="Underlying ticker symbol (e.g. TREX)")
    parser.add_argument("months", type=int, help="Number of expiry months to fetch")
    parser.add_argument("max_per_month", type=int, help="Maximum contracts to collect per month")
    parser.add_argument("--poll-minutes", type=int, default=3, help="Minutes to poll for market data (default: 3)")
    parser.add_argument("--poll-interval", type=int, default=30, help="Seconds between polling attempts (default: 30)")
    args = parser.parse_args()
    return args.ticker.upper(), args.months, args.max_per_month, args.poll_minutes, args.poll_interval


# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------
def main() -> int:
    config: Config = None
    client: IBKRClient = None
    try:
        ticker, num_months, max_per_month, poll_minutes, poll_interval = parse_cli_args()
        config = Config.from_env()
        config = dataclasses.replace(config, poll_minutes=poll_minutes, poll_interval=poll_interval)
        logger = setup_logging(config)
        client = IBKRClient(config, logger)

        logger.info(f"Processing ticker: {ticker}, months: {num_months}, max/month: {max_per_month}, poll: {poll_minutes}m/{poll_interval}s")

        # 1) Resolve underlying conid and available months.
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
        contracts = collect_contracts(client, under_conid, months, stock.last, max_per_month, logger)
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

        # 5) Snapshot market fields with EXPLICIT mdType=1 for live data
        attach_snapshot_data(client, top_contracts, SNAPSHOT_FIELDS, logger, md_type="1")

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
    except Exception as exc:
        if config and hasattr(config, 'log_level'):
            logging.critical(f"Unhandled exception in main: {exc}")
        return 1
    finally:
        if client is not None:
            client.close()
            if config and hasattr(config, 'log_level'):
                logging.info("IBKR client session closed successfully.")


if __name__ == "__main__":
    sys.exit(main())