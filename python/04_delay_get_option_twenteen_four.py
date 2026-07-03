# 04_delay_get_option_twenteen_four.py
# -------------------------------
# Verbesserte Version – mehr Robustheit, besseres Error-Handling, zentrale Request-Methode
# -------------------------------

# ----------------------------------------------------------------------
# Imports
# ----------------------------------------------------------------------
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
from operator import itemgetter
from pathlib import Path
from functools import wraps

import requests

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
    preferred_exchanges: tuple[str, ...] = ("NASDAQ", "NYSE", "NYSE MKT", "BATS", "SMART", "AMEX")
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
        "84": "bid", "85": "ask", "86": "delta",
        "87": "gamma", "88": "theta", "89": "vega"
    }
    GENERIC_MAP = {
        "100": "volume", "101": "open_interest",
        "104": "historical_volatility", "106": "implied_volatility"
    }
    REQUIRED_FIELDS = (*FIELD_MAP.keys(),)

    def __init__(self, config: Config, logger: logging.Logger):
        self.config = config
        self.logger = logger
        self._session: Optional[requests.Session] = None

    @property
    def session(self) -> requests.Session:
        if self._session is None:
            self._session = requests.Session()
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
                        self.logger.info(f"Fallback exchange: {contract.get('description', 'Unknown')}")
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
        self, conid: int, month: str, strike: float, right: str = "P", exchange: str = "SMART"
    ) -> Result:
        result = self._get(
            f"/secdef/info?conid={conid}&month={month}&strike={strike}&secType=OPT&right={right}&exchange={exchange}"
        )
        if not result.ok:
            return result

        contracts = []
        for c in result.data:
            if c.get("strike") == strike:
                contracts.append(OptionContract(
                    conid=c["conid"],
                    symbol=c["symbol"],
                    strike=c["strike"],
                    maturity_date=c.get("maturityDate", ""),
                    right=c.get("right", right),
                ))
        return Result.success(contracts)

    def _snapshot_single_field(self, conids: List[int], field: str) -> Result:
        """Fetch a snapshot for the given conids for a SINGLE field, retrying on transient errors."""
        endpoint = f"/marketdata/snapshot?conids={','.join(map(str,conids))}&fields={field}&snapshot=0"
        if not conids:
            return Result.success({})
        auth = self.authenticate()
        if not auth.ok:
            return Result.failure(f"Auth failed: {auth.error}")
        for attempt in range(3):  # max 3 attempts
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
                    self.logger.warning(f"Request failed (attempt {attempt + 1}/3): {e}. Retrying in 3s...")
                    time.sleep(3)
                else:
                    return Result.failure(f"Request failed after 3 attempts: {e}")
        return Result.failure("Unexpected exit from retry loop")

    def _snapshot_raw(self, conids: List[int], fields: str) -> Result:
        """Fetch a snapshot for the given conids & fields, retrying on transient errors (max 3 attempts, 3s wait)."""
        if not conids:
            return Result.success({})
        field_list = [f.strip() for f in fields.split(",") if f.strip()]
        merged: Dict[int, Dict[str, Any]] = {cid: {"conid": cid} for cid in conids}

        for fid in field_list:
            result = self._snapshot_single_field(conids, fid)
            if not result.ok:
                self.logger.warning(f"Failed to fetch field {fid}: {result.error}")
                continue

            data = result.data
            items = data if isinstance(data, list) else [data]
            for item in items:
                if isinstance(item, dict):
                    cid = item.get("conid")
                    if cid in merged:
                        val = item.get(fid)
                        if val is not None and val != "":
                            merged[cid][fid] = val

        return Result.success(list(merged.values()))

    def get_stock_price(self, conid: int, symbol: str) -> Result:
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
                
                    self.logger.debug(f"API response for {symbol}: {data}")
                
                    if not data or not isinstance(data, list) or len(data) == 0:
                        self.logger.warning(f"Empty or invalid response, attempt {attempt + 1}/{self.config.max_retries}")
                        if attempt < self.config.max_retries - 1:
                            time.sleep(3)
                            continue
                        return Result.failure("No data received from API")
                
                    item = data[0]
                
                    # Validate all required fields exist and are non-empty
                    required_fields = {"31": "last_price", "84": "bid", "86": "ask"}
                    missing_fields = []
                    invalid_fields = []
                
                    for field_id, field_name in required_fields.items():
                        val = item.get(field_id)
                        if val is None or val == "":
                            missing_fields.append(field_name)
                        else:
                            try:
                                float(val)
                            except (ValueError, TypeError):
                                invalid_fields.append(field_name)
                
                    if missing_fields or invalid_fields:
                        self.logger.warning(f"Missing fields: {missing_fields}, Invalid fields: {invalid_fields}, attempt {attempt + 1}/{self.config.max_retries}")
                        if attempt < self.config.max_retries - 1:
                            time.sleep(3)
                            continue
                        # Use fallback logic for missing/invalid fields
                        bid = float(item.get("84")) if item.get("84") else None
                        ask = float(item.get("86")) if item.get("86") else None
                        if bid and ask:
                            last = (bid + ask) / 2.0
                            self.logger.info(f"Using bid/ask midpoint as last: {last}")
                            return Result.success(StockPrice(symbol=symbol, conid=conid, last=last, bid=bid, ask=ask))
                        return Result.failure(f"Missing: {missing_fields}, Invalid: {invalid_fields}")
                
                    # All fields are valid - return success
                    last = float(item["31"])
                    bid = float(item["84"]) if item.get("84") else None
                    ask = float(item["86"]) if item.get("86") else None
                    return Result.success(StockPrice(symbol=symbol, conid=conid, last=last, bid=bid, ask=ask))
                
                except requests.RequestException as e:
                    self.logger.error(f"Request error: {e}")
                    if attempt < self.config.max_retries - 1:
                        time.sleep(3)
                    else:
                        return Result.failure(f"Request failed: {e}")
                except json.JSONDecodeError as e:
                    self.logger.error(f"JSON decode error: {e}")
                    if attempt < self.config.max_retries - 1:
                        time.sleep(3)
                    else:
                        return Result.failure(f"Invalid JSON: {e}")
                except Exception as e:
                    self.logger.error(f"Unexpected error: {e}")
                    if attempt < self.config.max_retries - 1:
                        time.sleep(3)
                    else:
                        return Result.failure(f"Error: {e}")
        
            return Result.failure("Max retries exceeded - this should not be reached")

    def is_market_open(self) -> Result:
        """Check if US market is currently open (9:30 AM - 4:00 PM ET, Monday-Friday)"""
        try:
            from datetime import datetime, time
            import pytz

            endpoint = "/iserver/marketdata/status"
            auth = self.authenticate()
            if not auth.ok:
                eastern = pytz.timezone('US/Eastern')
                now = datetime.now(eastern)
                if now.weekday() >= 5:
                    return Result.failure("Market is closed (weekend)")
                market_open = time(9, 30)
                market_close = time(16, 0)
                current_time = now.time()
                if market_open <= current_time <= market_close:
                    return Result.success(True)
                else:
                    return Result.failure(f"Market is closed (current time ET: {now.strftime('%H:%M')})")

            result = self._get(endpoint)
            if not result.ok:
                eastern = pytz.timezone('US/Eastern')
                now = datetime.now(eastern)
                if now.weekday() >= 5:
                    return Result.failure("Market is closed (weekend)")
                market_open = time(9, 30)
                market_close = time(16, 0)
                current_time = now.time()
                if market_open <= current_time <= market_close:
                    return Result.success(True)
                else:
                    return Result.failure(f"Market is closed (current time ET: {now.strftime('%H:%M')})")

            data = result.data
            if isinstance(data, dict) and 'market' in data:
                market_status = data['market']
                if market_status == 'open':
                    return Result.success(True)
                else:
                    return Result.failure(f"Market is {market_status}")
            else:
                eastern = pytz.timezone('US/Eastern')
                now = datetime.now(eastern)
                if now.weekday() >= 5:
                    return Result.failure("Market is closed (weekend)")
                market_open = time(9, 30)
                market_close = time(16, 0)
                current_time = now.time()
                if market_open <= current_time <= market_close:
                    return Result.success(True)
                else:
                    return Result.failure(f"Market is closed (current time ET: {now.strftime('%H:%M')})")

        except ImportError:
            from datetime import datetime, time
            now = datetime.now()
            if now.weekday() >= 5:
                return Result.failure("Market is closed (weekend)")
            market_open = time(9, 30)
            market_close = time(16, 0)
            current_time = now.time()
            if market_open <= current_time <= market_close:
                return Result.success(True)
            else:
                return Result.failure(f"Market is closed (current time: {now.strftime('%H:%M')})")
        except Exception as e:
            return Result.failure(f"Error checking market status: {e}")

# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
def rotate_log(path: str, logger: logging.Logger) -> None:
    log_path = Path(path)
    if not log_path.exists():
        return

    max_rotation = 0
    for i in range(1, 6):
        rotated_path = Path(f"{path}.{i}")
        if rotated_path.exists():
            max_rotation = i
        else:
            break

    if max_rotation >= 5:
        oldest = Path(f"{path}.5")
        if oldest.exists():
            oldest.unlink()

    for i in range(max_rotation, 0, -1):
        old_path = Path(f"{path}.{i}")
        new_path = Path(f"{path}.{i+1}")
        if old_path.exists():
            old_path.rename(new_path)

    log_path.rename(Path(f"{path}.1"))
    logger.info(f"Rotated log file to {path}.1")

def write_debug_log(contracts: List[OptionContract], path: str, logger: logging.Logger) -> Result:
    try:
        rotate_log(path, logger)

        with open(path, "w", encoding="utf-8") as f:
            f.write(f"# Debug log created at {datetime.now().isoformat()}\n")
            f.write(f"# Total contracts: {len(contracts)}\n")
            for c in contracts:
                row = c.to_csv_row()
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        logger.info(f"Debug log written to {path}")
        return Result.success(True)
    except Exception as e:
        logger.error(f"Failed to write debug log: {e}")
        return Result.failure(str(e))

def write_csv(contracts: List[OptionContract], path: str, logger: logging.Logger) -> Result:
    headers = [
        "conid", "symbol", "right", "strike", "maturity_date",
        "bid", "ask", "delta", "gamma", "theta", "vega",
        "volume", "open_interest", "historical_volatility", "implied_volatility"
    ]
    try:
        with open(path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=headers)
            writer.writeheader()
            for c in contracts:
                writer.writerow(c.to_csv_row())
        logger.info(f"✅ Options CSV saved to {path}")
        return Result.success(True)
    except Exception as e:
        logger.error(f"Failed to write CSV: {e}")
        return Result.failure(str(e))

def append_stock_price(stock: StockPrice, path: str, logger: logging.Logger) -> Result:
    try:
        exists = Path(path).exists()
        with open(path, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["timestamp", "symbol", "conid", "last", "bid", "ask"])
            if not exists:
                writer.writeheader()
            writer.writerow({
                "timestamp": stock.timestamp, "symbol": stock.symbol, "conid": stock.conid,
                "last": stock.last, "bid": stock.bid or "", "ask": stock.ask or ""
            })
        logger.info(f"Stock price appended to {path}")
        return Result.success(True)
    except Exception as e:
        logger.error(f"Failed to append stock price: {e}")
        return Result.failure(str(e))

def filter_delta(contract: OptionContract, config: Config) -> bool:
    if not config.filter_delta:
        return True
    delta = contract.delta
    if delta is None:
        return False
    return config.delta_min <= delta <= config.delta_max

def correct_put_greeks(contract: OptionContract) -> None:
    if contract.right != "P":
        return
    if contract.delta is not None:
        try:
            d = float(contract.delta)
            if 0 <= d <= 1:
                contract.delta = -d
        except (ValueError, TypeError):
            pass
    if contract.gamma is not None:
        try:
            g = float(contract.gamma)
            if g > 0:
                contract.gamma = -g
        except (ValueError, TypeError):
            pass
    if contract.theta is not None:
        try:
            t = float(contract.theta)
            if t > 0:
                contract.theta = -t
        except (ValueError, TypeError):
            pass
    if contract.vega is not None:
        try:
            v = float(contract.vega)
            if v > 0:
                contract.vega = -v
        except (ValueError, TypeError):
            pass

def collect_contracts(
    client: IBKRClient, under_conid: int, months: List[str], current_price: float, max_per_month: int = 10, logger: logging.Logger = None
) -> List[OptionContract]:
    logger = logger or logging.getLogger(__name__)
    all_contracts: List[OptionContract] = []

    for month in months:
        logger.info(f"Processing month: {month}")
        strikes_result = client.get_strikes(under_conid, month)
        if not strikes_result.ok:
            logger.error(f"Failed to get strikes for {month}: {strikes_result.error}")
            continue

        strikes = strikes_result.data
        strikes.reverse()
        count = 0
        for strike_str in strikes:
            if count >= max_per_month:
                break
            try:
                strike = float(strike_str)
            except ValueError:
                continue
            if strike > current_price:
                continue

            info_result = client.get_contract_info(under_conid, month, strike, right="P")
            if not info_result.ok:
                logger.error(f"Failed to get contract info for strike {strike}: {info_result.error}")
                continue

            for c in info_result.data:
                all_contracts.append(c)
                count += 1
                logger.info(f"Collected contract: conid={c.conid}, strike={c.strike}, expiry={c.maturity_date}")
            time.sleep(1)

    logger.info(f"Total contracts collected: {len(all_contracts)}")
    return all_contracts

def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python 04_delay_get_option_twenteen_three.py <TICKER> [NUM_MONTHS] [MAX_CONTRACTS_PER_MONTH]")
        return 1

    ticker = sys.argv[1].upper()
    num_months = int(sys.argv[2]) if len(sys.argv) >= 3 else 4
    max_per_month = int(sys.argv[3]) if len(sys.argv) >= 4 else 10

    config = Config.from_env()
    logger = setup_logging(config)

    logger.info(f"Processing ticker: {ticker}, months: {num_months}, max/month: {max_per_month}")

    client = IBKRClient(config, logger)

    # ------------------- Find underlying conid -------------------
    search_result = client.search_secdef(ticker)  # Fixed typo: search_secdef instead of search_secdec
    if not search_result.ok:
        logger.error(f"Search failed: {search_result.error}")
        return 1

    under_conid = search_result.data.under_conid
    months = search_result.data.months[:num_months]
    logger.info(f"Underlying conid: {under_conid}, months: {months}")

    # ------------------- Get current price -------------------
    stock_result = client.get_stock_price(under_conid, ticker)
    if not stock_result.ok:
        logger.error(f"Stock price failed: {stock_result.error}")
        return 1

    stock = stock_result.data
    logger.info(f"Current price: {stock.last}")
    append_stock_price(stock, config.stock_price_csv, logger)

    # ------------------- Collect option contracts -------------------
    contracts = collect_contracts(client, under_conid, months, stock.last, max_per_month, logger)
    if not contracts:
        logger.warning("No contracts found. Writing empty CSV.")
        write_csv([], config.csv_output, logger)
        return 0

    # Sort & filter
    contracts.sort(key=lambda c: c.maturity_date)
    filtered = [c for c in contracts if c.strike < stock.last]
    filtered.sort(key=lambda c: c.strike, reverse=True)
    top_contracts = filtered[:10]

    if not top_contracts:
        logger.warning("No contracts with strike < current price.")
        write_csv([], config.csv_output, logger)
        # --------------------------------------------------------------
        # 1️⃣  Pull market snapshot - each field separately
        # --------------------------------------------------------------
        conids = [c.conid for c in top_contracts]

        # Define API field ID → OptionContract attribute name mapping
        FIELD_TO_ATTR = {
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

        merged: Dict[int, Dict[str, Any]] = {cid: {"conid": cid} for cid in conids}

        # Fetch each field individually with "Wait-until-Filled" logic
        for field_id, attr_name in FIELD_TO_ATTR.items():
            logger.info(f"Fetching field {field_id} ({attr_name})...")
        
            # Attempt up to 3 times until values are filled for most contracts
            for attempt in range(3):
                result = client._snapshot_raw(conids, field_id)
                if not result.ok:
                    logger.warning(f"Attempt {attempt+1}/3: Field {field_id} snapshot failed: {result.error}")
                    time.sleep(2)
                    continue
            
                data = result.data
                        if not isinstance(data, list):
                            data = [data]
        
                        filled_count = 0
                        for item in data:
                            if isinstance(item, dict):
                                cid = item.get("conid")
                                if cid in merged:
                                    field_value = item.get(field_id)
                                    if field_value is not None and field_value != "":
                                        merged[cid][attr_name] = field_value
                                        filled_count += 1
            
                # If we filled most of the contracts, we can stop retrying this field
                if filled_count >= len(conids) * 0.8:
                    logger.info(f"Field {field_id} successfully filled for {filled_count}/{len(conids)} contracts. Moving on.")
                    break
                else:
                    logger.info(f"Field {field_id} only filled for {filled_count}/{len(conids)}. Retrying in 3s...")
                    time.sleep(3)

        # Apply accumulated data to OptionContract objects
        for c in top_contracts:
            attrs = merged.get(c.conid)
            if attrs:
                for attr, value in attrs.items():
                    if attr != "conid" and hasattr(c, attr):
                        setattr(c, attr, value)

    # Re-correct Greeks
    for c in top_contracts:
        correct_put_greeks(c)

    # ------------------- Write debug log & CSV --------------------
    write_debug_log(top_contracts, config.debug_log, logger)
    csv_result = write_csv(top_contracts, config.csv_output, logger)
    if not csv_result.ok:
        return 1

    logger.info("Script completed successfully.")
    return 0

if __name__ == "__main__":
    sys.exit(main())