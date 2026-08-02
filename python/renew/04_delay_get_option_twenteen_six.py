#!/usr/bin/env python3
"""
Minimal option chain fetcher for IBKR TWS.
Collects put contracts for a single ticker and writes them to CSV.
No command‑line arguments, no delta filtering, minimal logging.
"""

import csv
import logging
import ib_insync as ib
from pathlib import Path

# ------------------------------------------------------------
# Simple configuration
# ------------------------------------------------------------
HOST = "127.0.0.1"
PORT = 7496
CLIENT_ID = 1
CSV_OUTPUT = "./options.csv"
DEBUG_LOG = "./option_debug.log"

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
            if contract.get(g) is not None:
                contract[g] = -contract[g]

# ------------------------------------------------------------
# Main workflow
# ------------------------------------------------------------
def main() -> None:
    # Connect to TWS
    ib_client = ib.IB()
    try:
        ib_client.connect(HOST, PORT, CLIENT_ID)
        log.info("Connected to TWS")

        # Resolve underlying contract (example: SPY)
        underlying = ib.Stock("SPY")
        details = ib_client.reqContractDetails(underlying)
        if not details:
            log.error("No contract details – is the ticker correct?")
            return
        under_conid = details[0].contract.conId
        log.info(f"Underlying conid: {under_conid}")

        # Get all option parameters for the underlying
        raw = ib_client.reqSecDefOptParams("", "", None, ["SPY"])
        expiries = raw[0] if raw else []
        if not expiries:
            log.warning("No expirations found")
            return

        # Collect put contracts (limit to 5 for simplicity)
        contracts = []
        collected = 0
        for exp in expiries:
            _, exp_date, strikes, _, _, types = exp
            for strike in strikes:
                for typ in types:
                    if typ != "P" or collected >= 5:
                        continue
                    opt = ib.Option("SPY", exp_date, strike, typ, "SMART")
                    opt_details = ib_client.reqContractDetails(opt)
                    for d in opt_details:
                        if d.contract.conId:
                            contract = {
                                "conid": d.contract.conId,
                                "symbol": "SPY",
                                "strike": strike,
                                "right": typ,
                                "last": "",
                                "delta": "",
                                "gamma": "",
                                "theta": "",
                                "vega": "",
                                "volume": "",
                                "open_interest": "",
                            }
                            flip_greeks(contract)
                            contracts.append(contract)
                            collected += 1
                            if collected >= 5:
                                break
                    if collected >= 5:
                        break
                if collected >= 5:
                    break

        if not contracts:
            log.warning("No put contracts collected")
            return

        # Write CSV (fixed column order)
        fieldnames = [
            "conid",
            "symbol",
            "strike",
            "right",
            "last",
            "delta",
            "gamma",
            "theta",
            "vega",
            "volume",
            "open_interest",
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
    finally:
        ib_client.disconnect()
        log.info("Disconnected from TWS")

if __name__ == "__main__":
    main()