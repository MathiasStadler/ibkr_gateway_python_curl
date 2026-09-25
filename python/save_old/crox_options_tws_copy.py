#!/usr/bin/env python3
"""
CROX Options Chain Fetcher - TWS Socket API (Port 7496)
Fetches option chain data with Greeks (delta, gamma) via native IB API (ibapi).
Connects to TWS on localhost:7496. Requires ibapi installed.

Usage:
    pip install ibapi --break-system-packages
    python3 crox_options_chain_tws_native.py --symbol CROX --output /home/hermes/crox_options.csv

Key notes:
    - ibapi 9.81+ flat layout: from ibapi.client import EClient, etc.
    - Market data fields: 84=bid, 85=ask, 86=delta, 87=gamma, 100=volume, 101=open_interest
    - Greeks require market open or delayed data enabled in TWS.
"""
import csv
import logging
import sys
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime, time as dt_time
from typing import Any, Dict, List, Optional

try:
    from ibapi.client import EClient
    from ibapi.contract import Contract
    from ibapi.wrapper import EWrapper
except ImportError:
    print("ERROR: ibapi not installed. Run: pip install ibapi --break-system-packages")
    sys.exit(1)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


@dataclass
class OptionContract:
    conid: int = 0
    symbol: str = ""
    strike: float = 0.0
    maturity_date: str = ""
    right: str = ""
    bid: Optional[float] = None
    ask: Optional[float] = None
    delta: Optional[float] = None
    gamma: Optional[float] = None
    volume: Optional[int] = None
    open_interest: Optional[int] = None
    ask_strike_ratio: Optional[float] = None

    def to_csv_row(self) -> Dict[str, Any]:
        row = asdict(self)
        for k, v in row.items():
            if v is None:
                row[k] = ""
        return row


class OptionChainWrapper(EWrapper):
    # Generic ticks: 27=delta, 28=gamma
    GENERIC_TICKS = "27,28"
    # Standard ticks: 1=bid, 2=ask, 4=last, 6=high, 7=low, 9=close
    STANDARD_TICKS = [1, 2, 4, 6, 7, 9]
    # Snapshot ticks: 100=volume, 101=open_interest
    SNAPSHOT_TICKS = [100, 101]

    def __init__(self):
        self.current_contracts: Dict[int, OptionContract] = {}
        self.field_data: Dict[int, Dict[str, Any]] = {}
        self.contract_details_complete = False
        self._client = None
        self._mkt_data_done = threading.Event()

    def set_client(self, client):
        self._client = client

    def error(self, reqId, errorCode, errorString, advancedOrderJson=""):
        if errorCode in (2104, 2106, 504):
            logger.debug(f"IB info {errorCode}: {errorString}")
        elif errorCode != 200:
            logger.warning(f"Error {errorCode} (reqId={reqId}): {errorString}")

    def contractDetails(self, reqId, contractDetails):
        c = contractDetails.contract
        logger.info(f"Found option: {c.symbol} {c.right} K={c.strike} exp={c.lastTradeDateOrContractMonth}")
        self.current_contracts[c.conId] = OptionContract(
            conid=c.conId, symbol=c.symbol, strike=c.strike,
            maturity_date=c.lastTradeDateOrContractMonth, right=c.right)

    def contractDetailsEnd(self, reqId):
        logger.info(f"Contract details complete: {len(self.current_contracts)} options")
        self.contract_details_complete = True
        if self.current_contracts and self._client:
            self._request_market_data()

    def contractDetailsEnd(self, reqId):
        logger.info(f"Contract details complete: {len(self.current_contracts)} options")
        self.contract_details_complete = True
        if self.current_contracts and self._client:
            self._request_market_data()

    def tickSnapshotEnd(self, reqId):
        logger.info(f"Snapshot complete for reqId={reqId}")
        self._mkt_data_done.set()

    def tickPrice(self, reqId, tickType, price, attrib):
        if reqId not in self.field_data:
            self.field_data[reqId] = {"conid": reqId}
        if tickType == 1:
            self.field_data[reqId]["bid"] = price
        elif tickType == 2:
            self.field_data[reqId]["ask"] = price
        elif tickType == 4:
            self.field_data[reqId]["last"] = price
        elif tickType == 6:
            self.field_data[reqId]["high"] = price
        elif tickType == 7:
            self.field_data[reqId]["low"] = price
        elif tickType == 9:
            self.field_data[reqId]["close"] = price

    def tickSize(self, reqId, tickType, size):
        if reqId not in self.field_data:
            self.field_data[reqId] = {"conid": reqId}
        if tickType == 100:
            self.field_data[reqId]["volume"] = size
        elif tickType == 101:
            self.field_data[reqId]["open_interest"] = size

    def tickGeneric(self, reqId, tickType, value):
        if reqId not in self.field_data:
            self.field_data[reqId] = {"conid": reqId}
        if tickType == 27:
            self.field_data[reqId]["delta"] = value
        elif tickType == 28:
            self.field_data[reqId]["gamma"] = value

    def _request_market_data(self):
        logger.info(f"Requesting market data for {len(self.current_contracts)} contracts...")
        req_id = 0
        for cid, opt in self.current_contracts.items():
            contract = Contract()
            contract.conId = cid
            contract.symbol = opt.symbol
            contract.secType = "OPT"
            contract.exchange = "SMART"
            contract.currency = "USD"
            contract.right = opt.right
            contract.strike = opt.strike
            contract.lastTradeDateOrContractMonth = opt.maturity_date
            req_id += 1
            self._client.reqMktData(req_id, contract, self.GENERIC_TICKS, True, False, [])
            time.sleep(0.05)
        logger.info(f"Waiting for market data snapshots (timeout 30s)...")
        self._mkt_data_done.wait(timeout=30)
        logger.info(f"Market data collection complete")


class OptionChainClient(EClient):
    def __init__(self, wrapper):
        EClient.__init__(self, wrapper)
        wrapper.set_client(self)

    def nextValidId(self, orderId):
        logger.info(f"Next valid order ID: {orderId}")


def is_market_open() -> bool:
    try:
        import pytz
        now = datetime.now(pytz.timezone("US/Eastern"))
    except ImportError:
        now = datetime.now()
    if now.weekday() >= 5:
        logger.warning("Market is closed (weekend)")
        return False
    if dt_time(9, 30) <= now.time() <= dt_time(16, 0):
        return True
    logger.warning(f"Market is closed (local time {now.strftime('%H:%M')})")
    return False


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Fetch options chain from TWS Socket API")
    parser.add_argument("--symbol", "-s", default="CROX")
    parser.add_argument("--output", "-o", default="/home/hermes/crox_options.csv")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7496)
    parser.add_argument("--clientId", type=int, default=100)
    args = parser.parse_args()

    wrapper = OptionChainWrapper()
    client = OptionChainClient(wrapper)
    client.connect(args.host, args.port, clientId=args.clientId)
    logger.info(f"🔌 Connected to TWS on {args.host}:{args.port}")

    # Force delayed market data for after-hours
    client.reqMarketDataType(3)
    logger.info("Market data type set to delayed (3)")

    # Start the EClient event loop in a daemon thread (ibapi 9.81+ pattern)
    threading.Thread(target=client.run, daemon=True).start()
    time.sleep(1)
    if not client.isConnected():
        logger.error("❌ TWS connection failed. Start TWS and enable API (port 7496).")
        return 1

    try:
        contract = Contract()
        contract.symbol = args.symbol
        contract.secType = "OPT"
        contract.exchange = "SMART"
        contract.currency = "USD"
        client.reqContractDetails(1, contract)

        start = time.time()
        while not wrapper.contract_details_complete and time.time() - start < 15:
            time.sleep(0.1)
        logger.info(f"✅ {len(wrapper.current_contracts)} options found")
        if not wrapper.current_contracts:
            logger.warning("No options found for symbol.")
            return 1

        start = time.time()
        while time.time() - start < 15:
            time.sleep(0.5)

        for conid, opt in wrapper.current_contracts.items():
            data = wrapper.field_data.get(conid, {})
            opt.bid = data.get("bid")
            opt.ask = data.get("ask")
            opt.delta = data.get("delta")
            opt.gamma = data.get("gamma")
            opt.volume = data.get("volume")
            opt.open_interest = data.get("open_interest")
            if opt.ask and opt.strike and opt.strike > 0:
                opt.ask_strike_ratio = round((opt.ask / opt.strike) * 100, 4)

        headers = ["conid", "symbol", "right", "strike", "maturity_date",
                   "bid", "ask", "delta", "gamma", "volume", "open_interest",
                   "ask_strike_ratio"]
        with open(args.output, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=headers)
            writer.writeheader()
            for opt in wrapper.current_contracts.values():
                writer.writerow(opt.to_csv_row())
        logger.info(f"✅ Saved: {args.output}")
        return 0
    except Exception as e:
        logger.error(f"Error: {e}")
        return 1
    finally:
        client.disconnect()
        logger.info("🔌 Disconnected")


if __name__ == "__main__":
    sys.exit(main())