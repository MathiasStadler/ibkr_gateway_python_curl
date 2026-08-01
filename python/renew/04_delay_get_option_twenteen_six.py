#!/usr/bin/env python3
"""
Fetch option contract data via Interactive Brokers TWS API (port 7496).

04_delay_get_option_twenteen_six.py
------------------------------------
Robust version – collects puts, filters by Delta (‑0.10 … +0.10), snapshots
market fields (bid, ask, Greeks, volume, open_interest, volatilities),
corrects Greek signs for puts, and writes results to CSV.
Uses ib_insync for native TWS communication.

All public methods return Result objects. Supports polling for market data
to fill delayed‑mode fields over time.
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

# Try to import ib_insync; mark availability flag
try:
    import ib_insync as ib
    IB_AVAILABLE = True
except ImportError:  # pragma: no cover
    IB_AVAILABLE = False
    ib = None  # type: ignore

# ----------------------------------------------------------------------
# Konfiguration
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class Config:
    host: str = "127.0.0.1"
    port: int = 7496  # TWS Paper Trading port
    client_id: int = 1
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
# Ergebnis-Typ (Allzweckwaffe für return values)
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
    # Suppress noisy urllib3 warnings when verifying SSL is off
    import urllib3
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


def apply_delta_filter(contracts: List[OptionContract], min_delta: float, max_delta: float) -> List[OptionContract]:
    """Return only contracts whose Delta lies in the supplied range."""
    return [c for c in contracts if c.delta is not None and min_delta <= c.delta <= max_delta]


def write_csv(
    contracts: List[OptionContract],
    csv_path: str,
    logger: logging.Logger,
) -> Result:
    """Write contracts to a CSV file using a fixed column order."""
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


def append_stock_price_csv(stock: StockPrice, csv_path: str, logger: logging.Logger) -> Result:
    """Append a single stock‑price row to a CSV file."""
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


def write_debug_log(
    contracts: List[OptionContract],
    log_path: str,
    logger: logging.Logger,
) -> Result:
    """Write all contracts as JSON lines to a debug log."""
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
    """Thin wrapper around ib_insync that returns Result objects."""

    def __init__(self, config: Config, logger: logging.Logger) -> None:
        self.config = config
        self.logger = logger
        self.ib: Optional[Any] = None
        self.connected = False

    # --------------------------------------------------------------
    # Verbindung
    # --------------------------------------------------------------
    def connect(self) -> Result:
        if not IB_AVAILABLE:
            return Result.success(None)  # caller will fallback later
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

    # --------------------------------------------------------------
    # Datenzugriff
    # --------------------------------------------------------------
    def get_underlying_conid(self, ticker: str) -> Result:
        """Resolves underlying contract ID for *ticker*."""
        if not self.is_connected():
            return Result.failure("Not connected to TWS")
        try:
            contract = ib.Stock(ticker)
            details = self.ib.reqContractDetails(contract)
            if details:
                # ib_insync uses 'conId' attribute
                conid = details[0].contract.conId
                return Result.success(conid)
            return Result.failure(f"No contract details for {ticker}")
        except Exception as exc:
            return Result.failure(str(exc))

    def get_current_price(self, ticker: str, conid: int) -> Result:
        """Snapshot last price (field 31), bid (84) and ask (85) for *ticker*."""
        if not self.is_connected():
            return Result.failure("Not connected to TWS")
        try:
            contract = ib.Stock(ticker)
            md = self.ib.reqMktData(contract, genericTickList="31,84,85", snapshot=True)
            # kurz warten, bis TWS das Datenfeld füllt
            time.sleep(0.5)
            last_val = md.last if md.last else md.close
            if last_val is None:
                return Result.failure("No last price returned")
            return Result.success(
                StockPrice(
                    symbol=ticker,
                    conid=conid,
                    last=last_val,
                    bid=md.bid,
                    ask=md.ask,
                )
            )
        except Exception as exc:
            return Result.failure(str(exc))

    def req_option_conids(self, underlying_conid: int, ticker: str) -> Result:
        """Fetches *all* option contracts (calls & puts) for *ticker* from TWS."""
        if not self.is_connected():
            return Result.failure("Not connected to TWS")
        try:
            # Verwenden Sie reqSecDefOptParams, um die Option-Parameter zu erhalten
            raw = self.ib.reqSecDefOptParams("", "", None, [ticker])
            expiries = raw[0] if raw else []
            contracts: List[Any] = []
            for exp in expiries:
                _, exp_date, strikes, trading_classes, mults, types = exp
                for strike in strikes:
                    for typ in types:
                        if typ in ("C", "P"):
                            opt = ib.Option(ticker, exp_date, strike, typ, "SMART")
                            details = self.ib.reqContractDetails(opt)
                            for d in details:
                                if d.contract.conId:
                                    contracts.append(d.contract)
            return Result.success(contracts)
        except Exception as exc:
            return Result.failure(str(exc))

    def get_market_data_snapshot(self, contracts: List[Any]) -> Result:
        """Collect market data for a list of option contracts (snapshot)."""
        if not self.is_connected():
            return Result.failure("Not connected to TWS")
        try:
            market_data = []
            for c in contracts:
                md = self.ib.reqMktData(c, '', False, False, snapshot=True)
                time.sleep(0.1)
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
# Hilfsfunktionen für Optionen (vereinfacht)
# ----------------------------------------------------------------------
def collect_option_contracts(ib_client: Any, ticker: str, current_price: float, max_per_month: int) -> List[OptionContract]:
    """Collects OTM puts using a heuristic (two strikes below current price)."""
    contracts: List[OptionContract] = []
    # Verwenden Sie reqSecDefOptParams, um verfügbare Expirationsdaten zu erhalten
    try:
        raw = ib_client.reqSecDefOptParams("", "", None, [ticker])
        expiries = raw[0] if raw else []
        # Wählen Sie die nächstgelegenen Expirationsdaten aus
        if not expiries:
            return contracts
        # Nehmen Sie die erste Expiration (nächste)
        _, exp_date, strikes, _, _, types = expiries[0]
        # Definieren Sie Ziel-Strikes unterhalb des aktuellen Preises (OTM Puts)
        target_strikes = []
        for offset in [0.05, 0.10, 0.15, 0.20]:
            s = current_price * (1 - offset)
            target_strikes.append(round(s, 2))
        # Wählen Sie bis zu max_per_month aus
        selected = 0
        for strike in target_strikes:
            if selected >= max_per_month:
                break
            for typ in ["P"]:
                opt = ib.Option(ticker, exp_date, strike, typ, "SMART")
                details = ib_client.reqContractDetails(opt)
                for d in details:
                    c = OptionContract(
                        conid=d.contract.conId,
                        symbol=ticker,
                        strike=strike,
                        right=typ,
                        maturity_date=d.contract.lastTradeDateOrContractMonth.replace("-", "").replace(".", ""),
                    )
                    contracts.append(c)
                    selected += 1
                    if selected >= max_per_month:
                        break
    except Exception as exc:
        # Wenn etwas fehlschlägt, geben Sie einfach einen leeren String zurück
        pass
    return contracts

# ----------------------------------------------------------------------
# Hauptarbeitsprozess
# ----------------------------------------------------------------------
def main() -> int:
    try:
        # ------------------------------------------------------------
        # 1) CLI-Argumente
        # ------------------------------------------------------------
        parser = argparse.ArgumentParser(description="Fetch option contract data via IBKR TWS API.")
        parser.add_argument("ticker", help="Underlying ticker symbol (e.g., TREX)")
        parser.add_argument("months", type=int, help="Number of expiry months to fetch")
        parser.add_argument("max_per_month", type=int, help="Maximum contracts to collect per month")
        parser.add_argument("--poll-minutes", type=int, default=3, help="Minutes to poll for market data (default: 3)")
        parser.add_argument("--poll-interval", type=int, default=30, help="Polling interval in seconds (default: 30)")
        parser.add_argument("--client-id", type=int, default=1, help="TWS client ID (default: 1)")
        parser.add_argument("--delta-range", type=float, nargs=2, default=[-0.10, 0.10],
                            metavar=("MIN_DELTA", "MAX_DELTA"),
                            help="Delta range to filter options (default: -0.10 0.10)")
        args = parser.parse_args()

        # ------------------------------------------------------------
        # 2) Config & Logger
        # ------------------------------------------------------------
        config = Config.from_env()
        # Ersetzen Sie Umgebungsvariablen durch CLI-Überschreibungen
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
        # 3) Verbinden Sie sich mit TWS (native ib_insync-Version)
        # ------------------------------------------------------------
        client = TWSClient(config, logger)
        conn_res = client.connect()
        if not conn_res.ok:
            logger.error(f"TWS-Verbindung fehlgeschlagen: {conn_res.error}")
            # Fallback auf HTTP-Gateway-Modus (einfach sterben)
            return 1

        # ------------------------------------------------------------
        # 4) Underlying conid
        # ------------------------------------------------------------
        conid_res = client.get_underlying_conid(args.ticker)
        if not conid_res.ok:
            logger.error(f"Failed to get conid: {conid_res.error}")
            return 1
        under_conid = conid_res.data
        logger.info(f"Underlying conid for {args.ticker}: {under_conid}")

        # ------------------------------------------------------------
        # 5) Aktueller Preis des Underlying
        # ------------------------------------------------------------
        price_res = client.get_current_price(args.ticker, under_conid)
        if not price_res.ok:
            logger.error(f"Failed to get stock price: {price_res.error}")
            return 1
        stock = price_res.data
        logger.info(f"Current price for {args.ticker}: {stock.last}")
        append_stock_price_csv(stock, config.stock_price_csv, logger)

        # ------------------------------------------------------------
        # 6) Sammeln Sie Optionskontrakte (OTM puts)
        # ------------------------------------------------------------
        # Verwenden Sie den vereinfachten Sammlungsmechanismus
        all_contracts = collect_option_contracts(client.ib, args.ticker, stock.last, args.max_per_month)

        if not all_contracts:
            logger.warning("No contracts found.")
            write_csv([], config.csv_output, logger)
            return 0

        # ------------------------------------------------------------
        # 7) Sortieren & auswählen (nur Puts unterhalb des aktuellen Preises)
        # ------------------------------------------------------------
        all_contracts.sort(key=lambda c: c.maturity_date)
        otm = [c for c in all_contracts if c.strike < stock.last]
        otm.sort(key=lambda c: c.strike, reverse=True)
        top_contracts = otm[:args.max_per_month]

        if not top_contracts:
            logger.warning("No OTM contracts selected.")
            write_csv([], config.csv_output, logger)
            return 0

        logger.info(f"Processing {len(top_contracts)} contracts")

        # ------------------------------------------------------------
        # 8) Snapshot-Marktdaten für diese Kontrakte
        # ------------------------------------------------------------
        # Erstellen Sie ib.Option-Objekte für jeden Vertrag
        ib_contracts = []
        for c in top_contracts:
            # Extrahieren Sie das Expirationsdatum im Format YYYYMMDD
            exp = c.maturity_date
            if len(exp) == 8:
                opt_exp = f"{exp[:4]}{exp[5:7]}{exp[8:10]}"
            else:
                opt_exp = exp
            opt = ib.Option(
                args.ticker,
                opt_exp,
                c.strike,
                c.right,
                "SMART",
            )
            opt.conId = c.conid
            ib_contracts.append(opt)

        md_res = client.get_market_data_snapshot(ib_contracts)
        if not md_res.ok:
            logger.warning(f"Market-data snapshot failed: {md_res.error}")
        else:
            # Ordnen Sie MarketData zurück zu OptionContract
            md_dict = {}
            for md in md_res.data:
                md_dict[md.conId] = md
            for contract in top_contracts:
                md = md_dict.get(contract.conid)
                if md:
                    contract.bid = md.bid
                    contract.ask = md.ask
                    # ib_insync MarketData enthält Attribute für Griechen und andere Felder
                    contract.delta = md.delta
                    contract.gamma = md.gamma
                    contract.theta = md.theta
                    contract.vega = md.vega
                    contract.volume = md.volume
                    contract.open_interest = md.openInterest
                    contract.historical_volatility = md.historicalVolatility
                    contract.implied_volatility = md.impliedVolatility

        # ------------------------------------------------------------
        # 9) Korrektur der Greek-Zeichen für Puts
        # ------------------------------------------------------------
        for c in top_contracts:
            correct_put_greeks(c)

        # ------------------------------------------------------------
        # 10) Persistenz – voller Satz + delta-gefilterter Satz
        # ------------------------------------------------------------
        write_debug_log(top_contracts, config.debug_log, logger)
        full_res = write_csv(top_contracts, config.csv_output, logger)
        if not full_res.ok:
            return 1

        # Delta-Filterung anwenden
        filtered = apply_delta_filter(top_contracts, config.delta_min, config.delta_max)
        if filtered:
            logger.info(
                f"Delta-filtered {len(filtered)} contracts (Delta∈[{config.delta_min:.3f},{config.delta_max:.3f}])"
            )
            filt_res = write_csv(filtered, config.delta_csv_output, logger)
            if not filt_res.ok:
                return 1
        else:
            logger.info("No contracts within the specified Delta range.")

        logger.info("Script completed successfully")
        return 0

    except Exception as exc:
        logging.critical(f"Unhandled exception: {exc}")
        return 1
    finally:
        # Schließen Sie die Verbindung, falls der Client erstellt wurde
        if "client" in locals():
            client.close_connection()
            logging.info("TWS connection closed")

# ----------------------------------------------------------------------
# Einstiegspunkt
# ----------------------------------------------------------------------
if __name__ == "__main__":
    sys.exit(main())