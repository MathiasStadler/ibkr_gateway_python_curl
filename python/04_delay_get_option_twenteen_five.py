#!/usr/bin/env python3
"""
Option Chain Scanner für IBKR Gateway (Port 4002).
Fetcht Optionen für einen Ticker und schreibt sie in eine CSV.
"""
import sys
import os
import csv
import time
import urllib3
from requests.adapters import HTTPAdapter
from requests.exceptions import RequestException
import requests

# Disable SSL warnings for self-signed certs
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ----------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------
BASE_URL = "https://localhost:4002/v1/api"
VERIFY_SSL = os.getenv("IBKR_VERIFY_SSL", "false").lower() == "true"
SESSION = None
ACCESS_TOKEN = None


def _get_session():
    global SESSION
    if SESSION is None:
        SESSION = requests.Session()
        adapter = HTTPAdapter(
            max_retries=3,
            pool_maxsize=50,
            pool_connections=10,
        )
        SESSION.mount("https://", adapter)
        SESSION.mount("http://", adapter)
        SESSION.headers.update({
            "Content-Type": "application/json",
            "Accept": "application/json",
        })
    return SESSION


def _do_request(path: str, method: str = "GET", json_data=None, expected_status=200):
    """Make a request to the IBKR Gateway API."""
    session = _get_session()
    url = f"{BASE_URL}{path}"
    kwargs = {"verify": VERIFY_SSL}
    if method == "GET":
        kwargs["params"] = json_data
    else:
        kwargs["json"] = json_data
    try:
        resp = session.request(method, url, **kwargs)
        if resp.status_code != expected_status:
            raise Exception(f"HTTP {resp.status_code}: {resp.text}")
        return resp.json()
    except RequestException as e:
        raise Exception(f"Request failed: {e}")


# ----------------------------------------------------------------------
# Authentication
# ----------------------------------------------------------------------

# ----------------------------------------------------------------------
# API Calls
# ----------------------------------------------------------------------
def search_secdef(symbol: str):
    """Search for option definitions by symbol."""
    try:
        data = _do_request(f"/secdef/search?symbol={symbol}")
        return data
    except Exception as e:
        print(f"Warning: search_secdef failed: {e}")
        return []


def get_option_snapshot(conid: int):
    """Fetch snapshot data for an option contract (last, bid, ask, deltas, etc.)."""
    try:
        # Fields: 31=last, 84=bid, 85=ask, 86=delta, 87=gamma, 88=theta, 89=vega
        data = _do_request(f"/marketdata/snapshot", {
            "conid": conid,
            "fields": "31,84,85,86,87,88,89"
        })
        return data
    except Exception as e:
        print(f"Warning: get_option_snapshot failed for conid {conid}: {e}")
        return None


def get_option_contracts(conid: int, expiry_month: str):
    """Get all option contracts for a specific conid and expiry (simulated)."""
    # Instead of fetching chains, request snapshots for known strikes/rights
    # This is a workaround - the proper way is via /secdef/search with expiry filter
    results = []
    secdefs = search_secdef(str(conid // 100000))  # Rough approximation
    
    # Fallback: use a simple polling approach
    # AAPL conid is around 265598, we iterate over possible strikes
    # For demo, we'll just request a few known contracts
    
    return results


# ----------------------------------------------------------------------
# Data Collection
# ----------------------------------------------------------------------
def collect_options(ticker: str, months: int, max_per_month: int):
    """Collect option chains for a ticker."""
    # Gateway läuft auf localhost, User ist bereits angemeldet
    all_rows = []
    
    # Step 1: Search for the underlying symbol
    secdefs = search_secdef(ticker)
    if not secdefs:
        print(f"Keine SecDef für {ticker} gefunden.")
        return []
    
    # Find the equity conid for the ticker
    equity_conid = None
    for sd in secdefs:
        if sd.get("secType") == "STK" and sd.get("symbol") == ticker.upper():
            equity_conid = sd.get("conid")
            break
    
    if not equity_conid:
        print(f"Konnte keine Aktie für {ticker} finden.")
        return []
    
    print(f"✓ Underlying conid: {equity_conid}")
    
    # Step 2: Get expiry list (next 3 months)
    exps = []
    for sd in secdefs:
        if sd.get("secType") == "OPT" and sd.get("symbol") == ticker.upper():
            exp = sd.get("expiry")
            if exp and exp not in exps:
                exps.append(exp)
    exps = sorted(exps)[:months]
    
    print(f"📅 Expiries: {exps}")
    
    # Step 3: Iterate expiries and fetch snapshot data
    for exp_month in exps:
        for sd in secdefs:
            if sd.get("secType") == "OPT" and sd.get("expiry") == exp_month:
                conid = sd.get("conid")
                strike = sd.get("strike")
                right = sd.get("right")
                
                snapshot = get_option_snapshot(conid)
                if snapshot:
                    row = {
                        "conid": conid,
                        "symbol": ticker,
                        "expiry": exp_month,
                        "strike": strike,
                        "right": right,
                        "exchange": sd.get("exchange", ""),
                        "last": snapshot.get(31),
                        "bid": snapshot.get(84),
                        "ask": snapshot.get(85),
                        "delta": snapshot.get(86),
                        "gamma": snapshot.get(87),
                        "theta": snapshot.get(88),
                        "vega": snapshot.get(89),
                        "volume": snapshot.get(80, ""),
                        "open_interest": snapshot.get(79, ""),
                        "implied_volatility": snapshot.get(61, ""),
                    }
                    all_rows.append(row)
                    
                    if len(all_rows) >= max_per_month * months:
                        break
        if len(all_rows) >= max_per_month * months:
            break
    
    return all_rows


# ----------------------------------------------------------------------
# CSV Output
# ----------------------------------------------------------------------
def write_csv(rows, filename):
    if not rows:
        print("❌ Keine Daten zum Schreiben.")
        return
    fieldnames = [
        "conid", "symbol", "expiry", "strike", "right", "exchange",
        "last", "bid", "ask", "delta", "gamma", "theta", "vega",
        "volume", "open_interest", "implied_volatility"
    ]
    with open(filename, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"✅ {len(rows)} Verträge in {filename} geschrieben.")


# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------
def main():
    if len(sys.argv) < 3:
        print("Usage: python3 %s <ticker> <months> [max_per_month]" % sys.argv[0])
        print("Example: python3 %s AAPL 1 10" % sys.argv[0])
        sys.exit(1)
    
    ticker = sys.argv[1]
    months = int(sys.argv[2])
    max_per_month = int(sys.argv[3]) if len(sys.argv) > 3 else 10
    
    print(f"📊 Option Chain Scanner für {ticker}")
    print(f"📅 Monate: {months}, Max pro Monat: {max_per_month}")
    
    rows = collect_options(ticker, months, max_per_month)
    
    if rows:
        filename = f"DelayOptionContracts_{ticker}_{int(time.time())}.csv"
        write_csv(rows, filename)
    else:
        print("❌ Keine Optionen gefunden.")


if __name__ == "__main__":
    main()