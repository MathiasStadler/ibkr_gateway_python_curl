#!/usr/bin/env python3
"""Fetch option contract data via Interactive Brokers TWS API.

04_delay_get_option_twenteen_six.py
------------------------------------
Robust version – collects puts, filters by Delta range, snapshots market
fields (bid, ask, Greeks, volume, open_interest, volatilities), corrects
Greek signs for puts, and writes results to CSV. Uses ib_insync for TWS
communication (port 7496 for Paper Trading). All public methods return
Result objects. Supports polling for market data to fill delayed-mode
fields over time.
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

import ib_insync as ib
import requests
from requests.adapters import HTTPAdapter
import urllib3

# ----------------------------------------------------------------------
# Konfiguration
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class Config:
    host: str = "127.0.0.1"
    port: int = 7496               # TWS Paper Trading port
    client_id: int = 1             # Unique client ID for TWS connection
    verify_ssl: bool = False
    request_timeout: int = 10
    max_retries: int = 3
    csv_output: str = "./DelayOptionContracts.csv"
    delta_csv_output: str = "./options_chain_delta_filtered.csv"
    debug_log: str = "./option_debug.log"
    stock_price_csv: str = "./stock_price.csv"
    poll_minutes: int = 3
    poll_interval: int = 30
    log_level: int = logging.INFO
    log_format: str = "%(asctime)s - %(levelname)s : %(lineno)d - %(message)s"
    delta_min: float = -0.10
    delta_max: float = 0.10

    @classmethod
    def from_env(cls) -> "Config":
        env = os.environ
        return cls(
            host=env.get("IBKR_HOST", "127.0.0.1"),
            port=int(env.get("IBKR_PORT", "7496")),
            client_id=int(env.get("IBKR_CLIENT_ID", "1")),
            verify_ssl=env.get("IBKR_VERIFY_SSL", "false").lower() in ("true", "1", "yes"),
            request_timeout=int(env.get("IBKR_TIMEOUT", "10")),
            max_retries=int(env.get("IBKR_MAX_RETRIES", "3")),
            log_level=getattr(logging, env.get("LOG_LEVEL", "INFO").upper(), logging.INFO),
            poll_minutes=int(env.get("POLL_MINUTES", "3")),
            poll_interval=int(env.get("POLL_INTERVAL", "30")),
            delta_min=float(env.get("DELTA_MIN", "-0.10")),
            delta_max=float(env.get("DELTA_MAX", "0.10")),
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
@dataclasses.dataclass
class StockPrice:
    symbol: str
    conid: int
    last: float
    bid: Optional[float] = None
    ask: Optional[float] = None
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclasses.dataclass
class OptionContract:
    conid: int
    symbol: str
    strike: float
    right: str = "P"
    maturity_date: str = ""
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
# Helper Functions
# ----------------------------------------------------------------------
def correct_put_greeks(contract: OptionContract) -> None:
    """Negate Greeks for put options (per convention)."""
    if contract.right.upper() == "P":
        if contract.delta is not None:
            contract.delta = -contract.delta
        if contract.gamma is not None:
            contract.gamma = -contract.gamma
        if contract.theta is not None:
            contract.theta = -contract.theta
        if contract.vega is not None:
            contract.vega = -contract.vega


def apply_delta_filter(contracts: List[OptionContract],
                       min_delta: float,
                       max_delta: float) -> List[OptionContract]:
    """Filter contracts based on Delta range."""
    return [c for c in contracts if min_delta <= c.delta <= max_delta]


def write_csv(contracts: List[OptionContract],
              csv_path: str,
              logger: logging.Logger) -> Result:
    """Write contracts to CSV using HEADER_ORDER."""
    HEADER_ORDER = [
        "conid", "symbol", "right", "strike", "maturity_date",
        "bid", "ask", "delta", "gamma", "theta", "vega",
        "volume", "open_interest", "historical_volatility", "implied_volatility",
    ]
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
        return Result.failure(str(exc))

def append_stock_price_csv(stock: StockPrice,
                           csv_path: str,
                           logger: logging.Logger) -> Result:
    """Append a single stock price row."""
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
        return Result.failure(str(exc))
    except Exception as exc:
        logger.error(f"Failed to append stock price to CSV: {exc}")
        return Result.failure(str(exc))

def write_debug_log(contracts: List[OptionContract],
                    log_path: str,
                    logger: logging.Logger) -> Result:
    """Write full contract list to JSON debug log."""
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
        return Result.failure(str(exc))

# ----------------------------------------------------------------------
# TWS Client Wrapper
# ----------------------------------------------------------------------
class TWSClient:
    """Wrapper around ib_insync IB client with Result-based methods."""

    def __init__(self, config: Config, logger: logging.Logger) -> None:
        self.config = config
        self.logger = logger
        self.ib: Optional[ib.IB] = None
        self.connected = False

    def connect(self) -> Result:
        if not IB_AVAILABLE:
            return Result.success(None)  # Will fall back later
        try:
            self.ib = ib.IB()
            self.ib.connect(
                host=self.config.host,
                port=self.config.port,
                clientId=self.config.client_id,
                readonly=True,
            )
            self.connected = True
            self.logger.info(f"Connected to TWS at {self.config.host}:{self.config.port}")
            return Result.success(self.ib)
        except Exception as exc:
            self.logger.error(f"Failed to connect to TWS: {exc}")
            return Result.failure(str(exc))

    def is_connected(self) -> bool:
        return self.connected and self.ib and self.ib.isConnected()

    def disconnect(self) -> None:
        if self.ib and self.is_connected():
            self.ib.disconnect()
            self.connected = False
            self.logger.info("TWS connection closed")

    def get_underlying_conid(self, ticker: str) -> Result:
        """Get conid for underlying ticker."""
        if not self.is_connected():
            return Result.failure("Not connected to TWS")
        try:
            contract = ib.Stock(ticker)
            details = self.ib.reqContractDetails(contract)
            if details:
                conid = details[0].contract.conid
                return Result.success(conid)
            return Result.failure(f"No contract details for {ticker}")
        except Exception as exc:
            return Result.failure(str(exc))

    def get_current_price(self, ticker: str, conid: int) -> Result:
        """Get current price for underlying using market data snapshot."""
        if not self.is_connected():
            return Result.failure("Not connected to TWS")
        try:
            contract = ib.Stock(ticker)
            md = self.ib.reqMarketData(contract, genericTickList="31,84,85", snapshot=True)
            # Allow a brief pause for data to arrive
            time.sleep(0.5)
            last_val = md.last if md.last else md.close
            if last_val is None:
                return Result.failure("No last price returned")
            bid = md.bid
            ask = md.ask
            return Result.success(StockPrice(symbol=ticker, conid=conid,
                                            last=last_val, bid=bid, ask=ask))
        except Exception as exc:
            return Result.failure(str(exc))

    def req_option_conids(self, underlying_conid: int,
                          ticker: str) -> Result:
        """Fetch all option contracts (calls & puts) for an underlying ticker."""
        if not self.is_connected():
            return Result.failure("Not connected to TWS")
        try:
            # Request option chain parameters
            opt_params = self.ib.reqSecDefOptParams("", "", None, [ticker])
            expiries = opt_params[0] if opt_params else []
            contracts: List[ib.Contract] = []
            for exp in expiries:
                _, exp_date, strikes, trading_classes, multipliers, available_types = exp
                for strike in strikes:
                    for typ in available_types:
                        if typ in ("C", "P"):
                            opt = ib.Option(ticker, exp_date, strike, typ, "SMART")
                            details = self.ib.reqContractDetails(opt)
                            for d in details:
                                if d.contract.conid:
                                    contracts.append(d.contract)
            return Result.success(contracts)
        except Exception as exc:
            return Result.failure(str(exc))

    def get_market_data_snapshot(self, contracts: List[ib.Contract]) -> Result:
        """Obtain a snapshot of market data for a list of contracts."""
        if not self.is_connected():
            return Result.failure("Not connected to TWS")
        try:
            market_data = []
            for c in contracts:
                md = self.ib.reqMktData(c, "", False, False)
                time.sleep(0.1)  # Small delay between each request
                market_data.append(md)
            return Result.success(market_data)
        except Exception as exc:
            return Result.failure(str(exc))

    def close_connection(self) -> None:
        if self.ib and self.is_connected():
            self.ib.disconnect()
            self.connected = False
            self.logger.info("TWS connection closed")


# ----------------------------------------------------------------------
# Main Logic
# ----------------------------------------------------------------------
def main() -> int:
    try:
        # ------------------------------------------------------------
        # 1) Parse CLI arguments (including delta range)
        # ------------------------------------------------------------
        parser = argparse.ArgumentParser(
            description="Fetch option contract data via IBKR TWS API."
        )
        parser.add_argument("ticker", help="Underlying ticker symbol (e.g., TREX)")
        parser.add_argument("months", type=int, help="Number of expiry months to fetch")
        parser.add_argument("max_per_month", type=int,
                            help="Maximum contracts to collect per month")
        parser.add_argument("--poll-minutes", type=int, default=3,
                            help="Minutes to poll for market data (default: 3)")
        parser.add_argument("--poll-interval", type=int, default=30,
                            help="Polling interval in seconds (default: 30)")
        parser.add_argument("--delta-range", type=float, nargs=2,
                            default=[-0.10, 0.10],
                            metavar=("MIN_DELTA", "MAX_DELTA"),
                            help="Delta range to filter options (default: -0.10 0.10)")
        parser.add_argument("--client-id", type=int, default=1,
                            help="TWS client ID (default: 1)")
        args = parser.parse_args()

        # ------------------------------------------------------------
        # 2) Setup configuration & logger
        # ------------------------------------------------------------
        config = Config.from_env()
        # Replace specific parts of config with CLI values
        config = dataclasses.replace(
            config,
            client_id=args.client_id,
            poll_minutes=args.poll_minutes,
            poll_interval=args.poll_interval,
            delta_min=args.delta_range[0],
            delta_max=args.delta_range[1],
        )
        logger = setup_logging(config)

        # ------------------------------------------------------------
        # 3) Connect to TWS
        # ------------------------------------------------------------
        client = TWSClient(config, logger)
        conn_res = client.connect()
        if not conn_res.ok:
            logger.error(f"TWS connection failed: {conn_res.error}")
            # Fallback to HTTP Gateway mode
            logger.info("Falling back to HTTP Gateway mode")
            return run_http_gateway_mode(
                ticker=args.ticker,
                months=args.months,
                max_per_month=args.max_per_month,
                config=config,
                logger=logger,
            )

        # ------------------------------------------------------------
        # 4) Resolve underlying conid
        # ------------------------------------------------------------
        conid_res = client.get_underlying_conid(args.ticker)
        if not conid_res.ok:
            logger.error(f"Failed to get conid: {conid_res.error}")
            return 1
        under_conid = conid_res.data
        logger.info(f"Underlying conid for {args.ticker}: {under_conid}")

        # ------------------------------------------------------------
        # 5) Get current market price of underlying
        # ------------------------------------------------------------
        price_res = client.get_current_price(args.ticker, under_conid)
        if not price_res.ok:
            logger.error(f"Failed to get stock price: {price_res.error}")
            return 1
        stock = price_res.data
        logger.info(f"Current price for {args.ticker}: {stock.last}")
        append_stock_price_csv(stock, config.stock_price_csv, logger)

        # ------------------------------------------------------------
        # 6) Collect option contracts (OTM puts up to max_per_month)
        # ------------------------------------------------------------
        all_contracts: List[OptionContract] = []
        current_price = stock.last

        # Simple heuristic: collect two strikes below current price
        strikes = [current_price * 0.95, current_price * 0.90]  # OTM puts
        for strike in strikes:
            opt = ib.Option(args.ticker,
                           datetime.now().strftime("%Y%m%d"),
                           strike,
                           "P",
                           "SMART")
            details = client.ib.reqContractDetails(opt)
            for d in details:
                c = OptionContract(
                    conid=d.contract.conid,
                    symbol=args.ticker,
                    strike=strike,
                    right="P",
                    maturity_date=d.contract.lastTradeDateOrContractMonth.replace("-",
                                                                 "").replace(".", ""),
                )
                all_contracts.append(c)

        if not all_contracts:
            logger.warning("No contracts found.")
            write_csv([], config.csv_output, logger)
            return 0

        # ------------------------------------------------------------
        # 7) Sort & select top contracts
        # ------------------------------------------------------------
        all_contracts.sort(key=lambda c: c.maturity_date)
        otm = [c for c in all_contracts if c.strike < current_price]
        otm.sort(key=lambda c: c.strike, reverse=True)
        top_contracts = otm[:args.max_per_month]

        if not top_contracts:
            logger.warning("No contracts meet OTM criteria.")
            write_csv([], config.csv_output, logger)
            return 0

        logger.info(f"Processing {len(top_contracts)} contracts")

        # ------------------------------------------------------------
        # 8) Snapshot market data for selected contracts
        # ------------------------------------------------------------
        contracts_to_fetch = []
        for c in top_contracts:
            opt = ib.Option(args.ticker,
                           c.maturity_date[:4] + c.maturity_date[6:8] + c.maturity_date[8:10],
                           c.strike, "P", "SMART")
            opt.conId = c.conid
            contracts_to_fetch.append(opt)

        md_res = client.get_market_data_snapshot(contracts_to_fetch)
        if not md_res.ok:
            logger.warning(f"Failed to fetch market data: {md_res.error}")
        else:
            # Populate fields from snapshot result
            md_items = md_res.data if isinstance(md_res.data, list) else [md_res.data]
            for md in md_items:
                if not isinstance(md, dict):
                    continue
                cid = md.get("conid")
                if cid is None:
                    continue
                for contract in top_contracts:
                    if contract.conid == cid:
                        for field_id in ["84", "85", "86", "87", "88", "89",
                                        "100", "101", "104", "106"]:
                            val = md.get(field_id)
                            if val is not None:
                                attr = FIELD_MAP.get(field_id)
                                if attr and hasattr(contract, attr):
                                    try:
                                        if isinstance(val, str) and "," in val:
                                            val = float(val.replace(",", "."))
                                        else:
                                            val = float(val)
                                        setattr(contract, attr, val)
                                    except (ValueError, TypeError):
                                        pass
        # ------------------------------------------------------------
        # 9) Correct Greek signs for puts
        # ------------------------------------------------------------
        for c in top_contracts:
            correct_put_greeks(c)

        # ------------------------------------------------------------
        # 10) Write outputs
        # ------------------------------------------------------------
        write_debug_log(top_contracts, config.debug_log, logger)
        csv_res = write_csv(top_contracts, config.csv_output, logger)
        if not csv_res.ok:
            return 1

        # ------------------------------------------------------------
        # 11) Filter by Delta range & write dedicated CSV
        # ------------------------------------------------------------
        filtered = apply_delta_filter(top_contracts,
                                      args.delta_range[0],
                                      args.delta_range[1])
        if filtered:
            logger.info(
                f"Delta‑filtered {len(filtered)} contracts "
                f"(Delta∈[{args.delta_range[0]:.3f},{args.delta_range[1]:.3f]})"
            )
            delta_csv_res = write_csv(filtered,
                                      config.delta_csv_output,
                                      logger)
            if not delta_csv_res.ok:
                return 1
        else:
            logger.info("No contracts within the specified Delta range.")

        logger.info("Script completed successfully")
        return 0

    except Exception as exc:
        logging.critical(f"Unhandled exception: {exc}")
        return 1
    finally:
        if "client" in locals():
            client.close_connection()
            logging.info("TWS connection closed")

# ----------------------------------------------------------------------
# Backwards‑compatible HTTP‑Gateway fallback (unchanged from original)
# ----------------------------------------------------------------------
def run_http_gateway_mode(ticker: str, months: int, max_per_month: int,
                          config: Config, logger: logging.Logger) -> int:
    logger.warning("Using HTTP Gateway fallback mode")
    result = download_with_gateway(
        ticker=ticker,
        months=months,
        max_per_month=max_per_month,
        config=config,
        logger=logger,
    )
    return 0 if result.ok else 1


def download_with_gateway(ticker: str, months: int, max_per_month: int,
                          config: Config, logger: logging.Logger) -> Result:
    """Legacy HTTP‑Gateway implementation (kept for compatibility)."""
    base_url = (f"https://{config.host}:{config.port}"
                if hasattr(config, "host") else "https://localhost:4002")
    base_url += config.ibkr_base_path if hasattr(config, "ibkr_base_path") else "/v1/api/iserver"

    session = requests.Session()
    try:
        # 1) Authenticate
        resp = session.get(f"{base_url}/accounts",
                           verify=config.verify_ssl,
                           timeout=config.request_timeout)
        resp.raise_for_status()
        logger.info("Authenticated to IB Gateway")

        # 2) Search for ticker
        resp = session.get(f"{base_url}/secdef/search?symbol={ticker}",
                           verify=config.verify_ssl,
                           timeout=config.request_timeout)
        resp.raise_for_status()
        data = resp.json()
        logger.info(f"Found {len(data)} contracts for {ticker}")

        contracts = []
        for item in data[:2]:
            if isinstance(item, dict):
                contracts.append(OptionContract(
                    conid=item.get("conid", 0),
                    symbol=ticker,
                    strike=item.get("strike", 0),
                    maturity_date=item.get("maturityDate", ""),
                    right="P",
                ))
                break

        write_csv(contracts, config.csv_output, logger)
        write_debug_log(contracts, config.debug_log, logger)
        return Result.success(True)

    except Exception as exc:
        logger.error(f"HTTP Gateway download failed: {exc}")
        return Result.failure(str(exc))
    finally:
        session.close()
        logger.info("HTTP session closed")

# ----------------------------------------------------------------------
# FIELD MAP (tick field id → attribute name)
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

BASIC_FIELDS: List[str] = ["84", "85", "86", "87", "88", "89", "100", "101"]
SNAPSHOT_FIELDS: List[str] = ["31", "84", "85", "86", "87", "89", "100", "101",
                              "104", "106"]
# ----------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------
if __name__ == "__main__":
    sys.exit(main())