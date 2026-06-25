# FOUND FROM HEERE
# https://www.interactivebrokers.com/campus/ibkr-quant-news/handling-options-chains/

import sys
import requests
import urllib3
import csv
import json
import time
import logging
from datetime import datetime
from operator import itemgetter

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s : %(lineno)d - %(message)s')
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

PREFERRED_EXCHANGES = ["NASDAQ", "NYSE", "NYSE MKT", "BATS", "SMART", "AMEX"]
FILTER_DELTA = False          # True = nur Deltas -0.50..-0.30, False = alle
FORCE_PUT_ONLY = True        # Entfernt alle Nicht-PUTs aus der CSV
AUTHENTICATE_DONE = False

# Session für bessere Performance (Connection Pooling)
_session = None

def get_session():
    """Get or create requests session."""
    global _session
    if _session is None:
        _session = requests.Session()
    return _session

def authenticate_market_data():
    """Authenticate to IBKR market data service. Returns (success, error)."""
    global AUTHENTICATE_DONE
    if AUTHENTICATE_DONE:
        return (True, None)

    logging.info("Checking authentication...")
    url = "https://localhost:4002/v1/api/iserver/accounts"
    try:
        resp = get_session().get(url, verify=False, timeout=10)
        resp.raise_for_status()
        AUTHENTICATE_DONE = True
        logging.info("✅ Market data session initialized.")
        return (True, None)
    except Exception as e:
        logging.error(f"❌ Failed to authenticate: {e}")
        return (False, str(e))


def get_stock_price(conid, symbol):
    """
    Fetch current stock price (underlying) via REST API.
    Returns (data, error) tuple.
    """
    logging.info(f"Fetching current stock price for {symbol} (conid={conid})...")
    
    success, err = authenticate_market_data()
    if not success:
        return (None, f"Market data authentication failed: {err}")

    url = "https://localhost:4002/v1/api/iserver/marketdata/snapshot"
    params = {
        "conids": conid,
        "fields": "31,84,86"   # 31 = last, 84 = bid, 86 = ask
    }
    try:
        resp = get_session().get(url, params=params, verify=False, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        
        if data and isinstance(data, list) and len(data) > 0:
            item = data[0]
            last = item.get("31", "N/A")
            bid = item.get("84", "N/A")
            ask = item.get("86", "N/A")
            logging.info(f"✅ {symbol} - Last: {last}, Bid: {bid}, Ask: {ask}")
            result = {
                "symbol": symbol,
                "conid": conid,
                "last": last,
                "bid": bid,
                "ask": ask,
                "timestamp": datetime.now().isoformat()
            }
            return (result, None)
        else:
            logging.warning(f"Unexpected response structure: {data}")
            return (None, "Unexpected response structure")
    except Exception as e:
        logging.error(f"Failed to fetch stock price for {symbol}: {e}")
        return (None, str(e))


def secdefSearch(symbol):
    """
    Search for option contract metadata.
    Returns (result, error) tuple with result containing underConid and months.
    """
    url = f'https://localhost:4002/v1/api/iserver/secdef/search?symbol={symbol}'
    try:
        search_request = get_session().get(url, verify=False, timeout=10)
        search_request.raise_for_status()
        data = search_request.json()
    except Exception as e:
        logging.error(f"API request failed for {symbol}: {e}")
        return (None, f"API request failed: {e}")

    if not isinstance(data, list):
        return (None, f"Unexpected API response for {symbol}: {data}")

    selected_contract = None
    for contract in data:
        if not isinstance(contract, dict):
            continue
        desc = contract.get("description", "")
        if desc in PREFERRED_EXCHANGES:
            for secType in contract.get("sections", []):
                if secType.get("secType") == "OPT":
                    selected_contract = contract
                    logging.info(f"Selected exchange: {desc}")
                    break
            if selected_contract:
                break
    if not selected_contract:
        for contract in data:
            if not isinstance(contract, dict):
                continue
            for secType in contract.get("sections", []):
                if secType.get("secType") == "OPT":
                    selected_contract = contract
                    logging.info(f"Fallback exchange: {contract.get('description', 'Unknown')}")
                    break
            if selected_contract:
                break

    if not selected_contract:
        return (None, f"No option contract found for {symbol}")

    underConid = selected_contract.get("conid")
    if not underConid:
        return (None, f"No conid for {symbol}")

    months = []
    for secType in selected_contract.get("sections", []):
        if secType.get("secType") == "OPT":
            months_str = secType.get("months", "")
            if months_str:
                months = months_str.split(';')
            break

    if not months:
        return (None, f"No option months for {symbol}")

    result = {"underConid": underConid, "months": months}
    return (result, None)


def secdefStrikes(underConid, month, exchange="SMART"):
    """
    Retrieve put strikes for a given month.
    Returns (strikes_list, error) tuple.
    """
    url = f'https://localhost:4002/v1/api/iserver/secdef/strikes?conid={underConid}&secType=OPT&month={month}&exchange={exchange}'
    try:
        strike_request = get_session().get(url, verify=False, timeout=10)
        strike_request.raise_for_status()
        strikes = strike_request.json().get("put", [])
        logging.info(f"Month {month}: {len(strikes)} Put strikes")
        return (strikes, None)
    except Exception as e:
        logging.error(f"Failed to fetch strikes for month {month}: {e}")
        return (None, str(e))


def secdefInfo(conid, month, strike, right="P", exchange="SMART"):
    """
    Fetch all options (including weeklies) for a given month and exact strike.
    Returns (contracts_list, error) tuple.
    """
    url = f'https://localhost:4002/v1/api/iserver/secdef/info?conid={conid}&month={month}&strike={strike}&secType=OPT&right={right}&exchange={exchange}'
    try:
        info_request = get_session().get(url, verify=False, timeout=10)
        info_request.raise_for_status()
        matching_contracts = []
        for contract in info_request.json():
            if contract.get("strike") == strike:
                contract_details = {
                    "conid": contract["conid"],
                    "symbol": contract["symbol"],
                    "strike": contract["strike"],
                    "maturityDate": contract.get("maturityDate"),
                    "right": contract.get("right", right)
                }
                matching_contracts.append(contract_details)
        return (matching_contracts, None)
    except Exception as e:
        logging.error(f"Failed to fetch option info for strike {strike}: {e}")
        return (None, str(e))


def get_option_snapshot_bulk(conids, fields="84,85", generic_ticks="100", max_attempts=5, delay=5, batch_size=10):
    """
    Retrieve market data snapshot for multiple contracts.
    Returns (data_dict, error) tuple.
    """
    if not conids:
        return ({}, None)

    success, err = authenticate_market_data()
    if not success:
        return (None, f"Market data authentication failed: {err}")

    field_map = {
        "84": "bid",
        "85": "ask",
        "86": "delta",
        "87": "gamma",
        "88": "theta",
        "89": "vega"
    }
    generic_map = {
        "100": "volume",
        "101": "open_interest",
        "104": "historical_volatility",
        "106": "implied_volatility"
    }

    all_data = {}
    # Normalize conids to list of ints
    if isinstance(conids, int):
        conid_list = [conids]
    elif isinstance(conids, (list, tuple)):
        conid_list = [int(c) for c in conids]
    else:
        return (None, "conids must be int or list of ints")

    total_batches = (len(conid_list) + batch_size - 1) // batch_size

    for i in range(0, len(conid_list), batch_size):
        batch = conid_list[i:i+batch_size]
        batch_num = i // batch_size + 1
        logging.info(f"Batch {batch_num}/{total_batches} ({len(batch)} contracts)")

        conid_str = ",".join(str(c) for c in batch)
        url = f'https://localhost:4002/v1/api/iserver/marketdata/snapshot?conids={conid_str}&fields={fields}&genericTickList={generic_ticks}&snapshot=0'
        logging.info(f"url mkt_date => {url}")

        batch_data = {}
        for attempt in range(max_attempts):
            try:
                resp = get_session().get(url, verify=False, timeout=10)
                resp.raise_for_status()
                data = resp.json()

                for item in data:
                    conid = item.get("conid")
                    if not conid:
                        continue
                    if conid not in batch_data:
                        batch_data[conid] = {}

                    for f_id, f_name in field_map.items():
                        val = item.get(f_id)
                        batch_data[conid][f_name] = val if val is not None else ""

                    for g_id, g_name in generic_map.items():
                        val = item.get(g_id)
                        batch_data[conid][g_name] = val if val is not None else ""

                # Second request to fill generic ticks if needed
                if attempt == 0:
                    time.sleep(1)
                    resp2 = get_session().get(url, verify=False, timeout=10)
                    resp2.raise_for_status()
                    data2 = resp2.json()
                    for item in data2:
                        conid = item.get("conid")
                        if not conid:
                            continue
                        if conid not in batch_data:
                            batch_data[conid] = {}
                        for g_id, g_name in generic_map.items():
                            val = item.get(g_id)
                            if val is not None:
                                batch_data[conid][g_name] = val

                # Check completeness
                complete = sum(1 for c in batch_data if all(f in batch_data[c] for f in field_map.values()))
                logging.info(f"Batch {batch_num}, attempt {attempt+1}: {complete}/{len(batch)} complete")
                if complete == len(batch):
                    break
                if attempt < max_attempts-1:
                    time.sleep(delay * (attempt+1))
            except Exception as e:
                logging.error(f"Batch {batch_num}, attempt {attempt+1} failed: {e}")
                if attempt < max_attempts-1:
                    time.sleep(delay)
                else:
                    logging.warning(f"Batch {batch_num} failed after {max_attempts} attempts")

        # Format data for this batch
        for conid, quote in batch_data.items():
            formatted = {}
            for f_name in field_map.values():
                val = quote.get(f_name, "")
                if f_name in ["bid", "ask"]:
                    formatted[f_name] = str(val) if val not in ["", None] else ""
                else:
                    formatted[f_name] = val if val not in ["", None] else ""
            for g_name in generic_map.values():
                formatted[g_name] = quote.get(g_name, "")
            all_data[conid] = formatted

        if i + batch_size < len(conid_list):
            time.sleep(1.5)

    return (all_data, None)


def write_debug_log(contracts_list, filename="option_debug.log"):
    """Write contracts list to debug log file. Returns (success, error)."""
    try:
        with open(filename, 'w', encoding='utf-8') as f:
            f.write(f"# Debug log created at {datetime.now().isoformat()}\n")
            f.write(f"# Total contracts: {len(contracts_list)}\n")
            for contract in contracts_list:
                try:
                    sanitized = {}
                    for k, v in contract.items():
                        if v is None:
                            sanitized[k] = None
                        elif isinstance(v, (str, int, float, bool)):
                            sanitized[k] = v
                        else:
                            sanitized[k] = str(v)
                    f.write(json.dumps(sanitized, ensure_ascii=False) + "\n")
                except Exception as e:
                    f.write(f"# Error serializing contract {contract.get('conid')}: {e}\n")
        logging.info(f"Debug log written to {filename} with {len(contracts_list)} entries.")
        return (True, None)
    except Exception as e:
        logging.error(f"Failed to write debug log: {e}")
        return (False, str(e))


def writeResult(filtered_contracts):
    """
    Write filtered contracts to CSV.
    Returns (success, error) tuple.
    """
    if not filtered_contracts:
        logging.warning("No contracts to process. CSV will be empty.")
        headers = ["conid", "symbol", "right", "month", "strike", "maturityDate",
                   "bid", "ask", "delta", "gamma", "theta", "vega",
                   "volume", "open_interest", "historical_volatility", "implied_volatility"]
        filePath = "./DelayOptionContracts.csv"
        try:
            with open(filePath, 'w', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=headers)
                writer.writeheader()
            logging.info(f"✅ Empty CSV saved to {filePath}")
            return (True, None)
        except Exception as e:
            logging.error(f"Failed to write empty CSV: {e}")
            return (False, str(e))

    conid_to_contract = {c["conid"]: c for c in filtered_contracts}
    all_conids = list(conid_to_contract.keys())
    logging.info(f"Fetching market data for {len(all_conids)} contracts...")
    snapshot_data, err = get_option_snapshot_bulk(all_conids)
    if err:
        logging.error(f"Market data fetch failed: {err}")
        return (False, f"Market data fetch failed: {err}")

    for conid, quote in snapshot_data.items():
        if conid in conid_to_contract:
            conid_to_contract[conid].update(quote)

    # Sign correction for Put options (if API returns positive values)
    for conid, contract in conid_to_contract.items():
        if contract.get("right") == "P":
            if "delta" in contract and contract["delta"]:
                try:
                    delta_val = float(contract["delta"])
                    if 0 <= delta_val <= 1:
                        contract["delta"] = -delta_val
                except (ValueError, TypeError):
                    pass
            if "gamma" in contract and contract["gamma"]:
                try:
                    gamma_val = float(contract["gamma"])
                    if gamma_val > 0:
                        contract["gamma"] = -gamma_val
                except (ValueError, TypeError):
                    pass

    success, err = write_debug_log(filtered_contracts)
    if not success:
        return (False, f"Debug log failed: {err}")

    logging.info("DEBUG: First 10 deltas (raw):")
    for i, c in enumerate(filtered_contracts[:10]):
        logging.info(f"  {i+1}: conid={c.get('conid')}, delta={c.get('delta')}, right={c.get('right')}")

    headers = ["conid", "symbol", "right", "month", "strike", "maturityDate",
               "bid", "ask", "delta", "gamma", "theta", "vega",
               "volume", "open_interest", "historical_volatility", "implied_volatility"]
    filePath = "./DelayOptionContracts.csv"
    try:
        with open(filePath, 'w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=headers)
            writer.writeheader()
            for c in filtered_contracts:
                row = {h: c.get(h, "") for h in headers}
                writer.writerow(row)
        logging.info(f"✅ Options CSV saved to {filePath}")
        return (True, None)
    except Exception as e:
        logging.error(f"Failed to write options CSV: {e}")
        return (False, str(e))


def save_stock_price_to_csv(stock_data):
    """Save stock price data to CSV. Returns (success, error) tuple."""
    if not stock_data:
        logging.warning("No stock data to save.")
        return (False, "No stock data")
    filePath = "./stock_price.csv"
    file_exists = False
    try:
        with open(filePath, 'r') as f:
            file_exists = True
    except FileNotFoundError:
        pass

    try:
        with open(filePath, 'a', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=["timestamp", "symbol", "conid", "last", "bid", "ask"])
            if not file_exists:
                writer.writeheader()
            writer.writerow(stock_data)
        logging.info(f"✅ Stock price appended to {filePath}")
        return (True, None)
    except Exception as e:
        logging.error(f"Failed to append stock price: {e}")
        return (False, str(e))


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python script.py <TICKER> [NUMBER_OF_MONTHS]")
        sys.exit(1)
    ticker = sys.argv[1].upper()
    num_months = int(sys.argv[2]) if len(sys.argv) >= 3 else 4

    logging.info(f"Processing ticker: {ticker}, next {num_months} months")
    logging.info(f"Delta filter: {'ON' if FILTER_DELTA else 'OFF'} (range -0.50 to -0.30)")

    # Search for contract
    result, err = secdefSearch(ticker)
    if err:
        logging.error(f"Failed to search for {ticker}: {err}")
        sys.exit(1)
    underConid = result["underConid"]
    months = result["months"]

    # Get stock price
    stock_data, err = get_stock_price(underConid, ticker)
    if err:
        logging.error(f"Failed to get stock price: {err}")
        sys.exit(1)
    save_stock_price_to_csv(stock_data)

    try:
        current_stock_price_float = float(stock_data['last'])
        logging.info(f"Aktueller Aktienkurs {ticker}: {current_stock_price_float}")
    except (ValueError, TypeError):
        logging.error("Kurs konnte nicht in float konvertiert werden. Filterung nach Strike nicht möglich.")
        sys.exit(1)

    if not months:
        logging.error(f"No option months found for {ticker}")
        sys.exit(1)

    selected_months = months[:num_months]
    logging.info(f"Selected months: {selected_months}")

    # Collect put contracts
    all_contracts = []
    counter = 0
    break_parent_for = 0
    for month in selected_months:
        if break_parent_for:
            logging.info(f"Break for loop at month => {month}")
            break
        logging.info(f"Processing {month}...")
        strikes, err = secdefStrikes(underConid, month)
        if err:
            logging.error(f"Failed to get strikes for month {month}: {err}")
            continue
        strikes.reverse()
        logging.info("use the first 10 item")
        for strike in strikes:
            if break_parent_for:
                logging.info(f"Break for loop at month => {month}")
                break
            strike = float(strike)
            logging.info(f"Test Strike=>{strike}")

            if strike > current_stock_price_float:
                logging.info(f"skip this strike => {strike} - over {current_stock_price_float}")
                continue

            contracts_all, err = secdefInfo(underConid, month, strike, right="P")
            if err:
                logging.error(f"Failed to get option info for strike {strike}: {err}")
                continue

            for c in contracts_all:
                all_contracts.append(c)
                counter += 1
                logging.info(f"all_contracts => {counter}")
                if counter > 10:
                    break_parent_for = 1
                    break
            time.sleep(1)

    logging.info(f"Total contracts fetched (before filtering by strike): {len(all_contracts)}")
    print(all_contracts)

    # Filter: Nächste 10 Strikes unter dem Aktienkurs
    lower_strikes = []
    logging.info(f"Typ of all_contracts {type(all_contracts)}")
    sorted_list = sorted(all_contracts, key=itemgetter('maturityDate'))
    print(sorted_list)
    all_contracts = sorted_list
    for contract in all_contracts:
        try:
            strike = float(contract.get("strike", 0))
            if strike < current_stock_price_float:
                lower_strikes.append(contract)
        except (ValueError, TypeError):
            logging.warning(f"Ungültiger Strike für Contract {contract.get('conid')}: {contract.get('strike')}")

    # Sort absteigend (höchste Strikes unter dem Kurs zuerst)
    lower_strikes.sort(key=lambda x: float(x.get("strike", 0)), reverse=True)

    top_10_underlying = lower_strikes[:10]
    logging.info(f"Nach Filter: {len(top_10_underlying)} Contracts (max 10) mit Strike < {current_stock_price_float}")

    if not top_10_underlying:
        logging.warning("Keine Kontrakte mit Strike unter dem Aktienkurs gefunden. CSV wird nur Kopfzeile enthalten.")

    writeResult(top_10_underlying)