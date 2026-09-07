#!/usr/bin/env python3
"""
Minimal option chain fetcher for IBKR TWS.
Collects put contracts for a single ticker and writes them to CSV.
Uses TWS port 4976 via SSH tunnel to trapapa@192.168.178.75:7496.

Usage:
    python3 04_delay_get_option_twenteen_six.py [TICKER] [NUM_MONTHS] [MAX_PER_MONTH]
    Default: TICKER=TREX NUM_MONTHS=1 MAX_PER_MONTH=5
"""

import csv
import logging
import sys
import time
from pathlib import Path

try:
    import ib_insync as ib  # type: ignore[import]
except ImportError:
    print("ERROR: ib_insync not installed.")
    print("Install: /home/hermes/.hermes/hermes-agent/venv/bin/pip install ib_insync")
    sys.exit(1)

# ------------------------------------------------------------
# Simple configuration
# ------------------------------------------------------------
HOST = "127.0.0.1"
PORT = 4976
CLIENT_ID = 1
CSV_OUTPUT = "./options.csv"
DEBUG_LOG = "./option_debug.log"

# CLI arguments
TICKER = sys.argv[1].upper() if len(sys.argv) > 1 else "TREX"
NUM_MONTHS = int(sys.argv[2]) if len(sys.argv) > 2 else 1
MAX_PER_MONTH = int(sys.argv[3]) if len(sys.argv) > 3 else 5

# ------------------------------------------------------------
# Logging (very lightweight)
# ------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    handlers=[logging.FileHandler(DEBUG_LOG), logging.StreamHandler()],
)
log = logging.getLogger(__name__)

# ------------------------------------------------------------
# Helper: flip Greeks for puts (per IB convention)
# ------------------------------------------------------------
def flip_greeks(contract: dict) -> None:
    if contract["right"] == "P":
        for g in ("delta", "gamma", "theta", "vega"):
            if contract.get(g) is not None and contract[g] != "":
                try:
                    contract[g] = -float(contract[g])
                except (ValueError, TypeError):
                    pass


# ------------------------------------------------------------
# Market data helpers
# ------------------------------------------------------------
def get_underlying_price(ib_client: ib.IB, stock: ib.Stock) -> float:
    """Get the current underlying stock price with retry."""
    for attempt in range(3):
        ib_client.reqMarketDataType(3)  # delayed data
        ticker = ib_client.reqMktData(stock, "", True, False)
        ib_client.sleep(3)
        last = ticker.marketPrice()
        if last == last and last > 0:  # not NaN and positive
            ib_client.cancelMktData(stock)
            log.info(f"Underlying price: {last}")
            return last
        ib_client.cancelMktData(stock)
        log.warning(f"Attempt {attempt+1}/3: price not available, retrying...")
        time.sleep(2)
    return 0.0


# ------------------------------------------------------------
# Main workflow
# ------------------------------------------------------------
def main() -> None:
    log.info(f"Scanning option chain for {TICKER} (months={NUM_MONTHS}, max/month={MAX_PER_MONTH})")

    # Connect to TWS
    ib_client = ib.IB()
    try:
        ib_client.connect(HOST, PORT, CLIENT_ID)
        log.info(f"Connected to TWS (port {PORT})")
    except Exception as e:
        log.error(f"Failed to connect to TWS: {e}")
        log.error("Make sure SSH tunnel is running:")
        log.error("  sshpass -p <pw> ssh -L 4976:127.0.0.1:7496 trapapa@192.168.178.75")
        return

    try:
        # Resolve underlying contract
        underlying = ib.Stock(TICKER, "SMART", "USD")
        qualified = ib_client.qualifyContracts(underlying)
        if not qualified:
            log.error(f"No contract details for {TICKER} – is the ticker correct?")
            return
        under_conid = qualified[0].conId
        log.info(f"Underlying conid: {under_conid}")

        # Get underlying price for ATM strike selection
        current_price = get_underlying_price(ib_client, underlying)
        if current_price <= 0:
            log.warning("Could not fetch price, will use all strikes")

        # Get option chain parameters
        # Note: ib_insync signature is (underlyingSymbol, futFopExchange, underlyingSecType, underlyingConId)
        chains = ib_client.reqSecDefOptParams(TICKER, "", "STK", under_conid)
        if not chains:
            log.warning("No option chains found")
            return

        # Use the SMART chain (or first one)
        chain = chains[0]
        log.info(f"Found {len(chain.expirations)} expirations on exchange {chain.exchange}")

        # Pick the next NUM_MONTHS unique expiration dates
        expirations = sorted(set(chain.expirations))[:NUM_MONTHS]
        log.info(f"Selected expirations: {expirations}")

        # Filter strikes: ±20% around current price (if price available)
        if current_price > 0:
            min_strike = current_price * 0.80
            max_strike = current_price * 1.20
            filtered_strikes = sorted(
                [s for s in chain.strikes if min_strike <= s <= max_strike]
            )
            log.info(f"Strikes in ±20% range: {len(filtered_strikes)} of {len(chain.strikes)}")
        else:
            filtered_strikes = sorted(chain.strikes)

        # Take the top MAX_PER_MONTH strikes closest to ATM
        if current_price > 0 and filtered_strikes:
            filtered_strikes.sort(key=lambda s: abs(s - current_price))
            selected_strikes = filtered_strikes[:MAX_PER_MONTH]
        else:
            selected_strikes = filtered_strikes[:MAX_PER_MONTH]

        # Build contracts
        contracts = []
        for expiry in expirations:
            for strike in selected_strikes:
                for right in ("P", "C"):
                    opt = ib.Option(
                        TICKER, expiry, strike, right,
                        exchange=chain.exchange,
                        currency="USD",
                        tradingClass=chain.tradingClass,
                        multiplier="100"
                    )
                    qualified_opts = ib_client.qualifyContracts(opt)
                    if qualified_opts:
                        c = qualified_opts[0]
                        contracts.append({
                            "conid": c.conId,
                            "symbol": TICKER,
                            "expiry": expiry,
                            "strike": strike,
                            "right": right,
                            "exchange": chain.exchange,
                            "last": "",
                            "bid": "",
                            "ask": "",
                            "delta": "",
                            "gamma": "",
                            "theta": "",
                            "vega": "",
                            "volume": "",
                            "open_interest": "",
                            "implied_volatility": "",
                        })

        log.info(f"Built {len(contracts)} contract objects")

        if not contracts:
            log.warning("No contracts built")
            return

        # Fetch market data (delayed = type 3 for paper trading)
        ib_client.reqMarketDataType(3)
        ib_contracts = []
        for c in contracts:
            opt = ib.Option(
                c["symbol"], c["expiry"], c["strike"], c["right"],
                exchange=c["exchange"],
                currency="USD",
                tradingClass=chain.tradingClass,
                multiplier="100"
            )
            qualified = ib_client.qualifyContracts(opt)
            if qualified:
                ib_contracts.append(qualified[0])

        # Request tickers in batches
        BATCH = 25
        for i in range(0, len(ib_contracts), BATCH):
            batch = ib_contracts[i:i+BATCH]
            tickers = ib_client.reqTickers(*batch)
            log.info(f"Fetching market data for batch {i//BATCH+1} ({len(batch)} contracts)...")
            ib_client.sleep(8)  # wait for delayed data to populate

            for t, c in zip(tickers, contracts[i:i+BATCH]):
                # Extract fields safely
                if t.last == t.last and t.last != -1:
                    c["last"] = round(t.last, 4)
                if t.bid == t.bid and t.bid != -1:
                    c["bid"] = round(t.bid, 4)
                if t.ask == t.ask and t.ask != -1:
                    c["ask"] = round(t.ask, 4)
                if t.volume == t.volume and t.volume != -1:
                    c["volume"] = int(t.volume)
                if hasattr(t, 'openInterest') and t.openInterest == t.openInterest and t.openInterest != -1:
                    c["open_interest"] = int(t.openInterest)
                if hasattr(t, 'impliedVol') and t.impliedVol == t.impliedVol and t.impliedVol != -1:
                    c["implied_volatility"] = round(t.impliedVol, 4)

                # Greeks
                if hasattr(t, 'modelGreeks') and t.modelGreeks:
                    g = t.modelGreeks
                    if g.delta == g.delta and g.delta != 0:
                        c["delta"] = round(g.delta, 4)
                    if g.gamma == g.gamma and g.gamma != 0:
                        c["gamma"] = round(g.gamma, 4)
                    if g.theta == g.theta and g.theta != 0:
                        c["theta"] = round(g.theta, 4)
                    if g.vega == g.vega and g.vega != 0:
                        c["vega"] = round(g.vega, 4)

                flip_greeks(c)

            ib_client.cancelMktData(*batch)

        # Write CSV
        fieldnames = [
            "conid", "symbol", "expiry", "strike", "right", "exchange",
            "last", "bid", "ask", "delta", "gamma", "theta", "vega",
            "volume", "open_interest", "implied_volatility",
        ]
        Path(CSV_OUTPUT).parent.mkdir(parents=True, exist_ok=True)
        with open(CSV_OUTPUT, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for c in contracts:
                writer.writerow({k: c.get(k, "") for k in fieldnames})

        log.info(f"Wrote {len(contracts)} contracts to {CSV_OUTPUT}")

    except Exception as e:
        log.error(f"Unexpected error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        ib_client.disconnect()
        log.info("Disconnected from TWS")


if __name__ == "__main__":
    main()
