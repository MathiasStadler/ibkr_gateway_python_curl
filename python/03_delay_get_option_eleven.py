# FOUND FROM HEERE
# https://www.interactivebrokers.com/campus/ibkr-quant-news/handling-options-chains/

import sys
import requests
import urllib3
import csv
import json
import pprint
import time
import logging
from datetime import datetime

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

PREFERRED_EXCHANGES = ["NASDAQ", "NYSE", "NYSE MKT", "BATS", "SMART", "AMEX"]
FILTER_DELTA = True          # True = nur Deltas -0.50..-0.30
FORCE_PUT_ONLY = True

def authenticate_market_data():
    logging.info("Checking authentication...")
    url = "https://localhost:4002/v1/api/iserver/accounts"
    try:
        resp = requests.get(url=url, verify=False)
        resp.raise_for_status()
        logging.info("✅ Market data session initialized.")
        return True
    except Exception as e:
        logging.error(f"❌ Failed: {e}")
        return False

def secdefSearch(symbol):
    url = f'https://localhost:4002/v1/api/iserver/secdef/search?symbol={symbol}'
    search_request = requests.get(url=url, verify=False)
    data = search_request.json()
    selected_contract = None
    for contract in data:
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
            for secType in contract.get("sections", []):
                if secType.get("secType") == "OPT":
                    selected_contract = contract
                    logging.info(f"Fallback exchange: {contract.get('description', 'Unknown')}")
                    break
            if selected_contract:
                break
    if not selected_contract:
        raise ValueError(f"No option contract found for {symbol}")
    underConid = selected_contract["conid"]
    months = []
    for secType in selected_contract.get("sections", []):
        if secType.get("secType") == "OPT":
            months = secType.get("months", "").split(';')
            break
    if not months:
        raise ValueError(f"No option months for {symbol}")
    return underConid, months

def secdefStrikes(underConid, month, exchange="SMART"):
    url = f'https://localhost:4002/v1/api/iserver/secdef/strikes?conid={underConid}&secType=OPT&month={month}&exchange={exchange}'
    strike_request = requests.get(url=url, verify=False)
    strikes = strike_request.json().get("put", [])
    logging.info(f"Month {month}: {len(strikes)} Put strikes")
    return strikes

def secdefInfo(conid, month, strike, right="P", exchange="SMART"):
    url = f'https://localhost:4002/v1/api/iserver/secdef/info?conid={conid}&month={month}&strike={strike}&secType=OPT&right={right}&exchange={exchange}'
    info_request = requests.get(url=url, verify=False)
    contracts = []
    for contract in info_request.json():
        contract_right = contract.get("right", right)
        contractDetails = {
            "conid": contract["conid"],
            "symbol": contract["symbol"],
            "strike": contract["strike"],
            "maturityDate": contract["maturityDate"],
            "right": contract_right
        }
        contracts.append(contractDetails)
    return contracts

def get_option_snapshot_bulk(conids, fields="84,85,86,87,88,89", max_attempts=2, delay=2, batch_size=10):
    if not conids:
        return {}
    authenticate_market_data()
    field_map = {"84": "bid", "85": "ask", "86": "delta", "87": "gamma", "88": "theta", "89": "vega"}
    all_data = {}
    total_batches = (len(conids) + batch_size - 1) // batch_size
    for i in range(0, len(conids), batch_size):
        batch = conids[i:i+batch_size]
        batch_num = i//batch_size + 1
        logging.info(f"Batch {batch_num}/{total_batches} ({len(batch)} contracts)")
        conid_str = ",".join(str(c) for c in batch)
        url = f'https://localhost:4002/v1/api/iserver/marketdata/snapshot?conids={conid_str}&fields={fields}&delay=1&snapshot=1'
        batch_data = {}
        for attempt in range(max_attempts):
            try:
                resp = requests.get(url=url, verify=False)
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
                        if val is not None:
                            batch_data[conid][f_name] = val
                complete = sum(1 for c in batch_data if all(f in batch_data[c] for f in field_map.values()))
                logging.info(f"Batch {batch_num}, attempt {attempt+1}: {complete}/{len(batch)} complete")
                if complete == len(batch):
                    break
                if attempt < max_attempts-1:
                    time.sleep(delay*(attempt+1))
            except Exception as e:
                logging.error(f"Batch {batch_num}, attempt {attempt+1} failed: {e}")
                if attempt < max_attempts-1:
                    time.sleep(delay)
        for conid, quote in batch_data.items():
            formatted = {}
            for f_name in field_map.values():
                val = quote.get(f_name)
                if f_name in ["bid","ask"]:
                    formatted[f_name] = str(val) if val is not None else ""
                else:
                    formatted[f_name] = val if val is not None else ""
            all_data[conid] = formatted
        if i + batch_size < len(conids):
            time.sleep(1.5)
    return all_data

def write_debug_log(contracts_list, filename="option_debug.log"):
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

def writeResult(contracts_list):
    conid_to_contract = {c["conid"]: c for c in contracts_list}
    all_conids = list(conid_to_contract.keys())
    logging.info(f"Fetching market data for {len(all_conids)} contracts...")
    snapshot_data = get_option_snapshot_bulk(all_conids)
    for conid, quote in snapshot_data.items():
        if conid in conid_to_contract:
            conid_to_contract[conid].update(quote)
    
    write_debug_log(contracts_list)
    
    logging.info("DEBUG: First 5 deltas (raw):")
    for i, c in enumerate(contracts_list[:5]):
        logging.info(f"  {i+1}: conid={c.get('conid')}, delta={c.get('delta')}, right={c.get('right')}")
    
    if FORCE_PUT_ONLY:
        put_contracts = [c for c in contracts_list if c.get("right") == "P"]
        logging.info(f"After right filter: {len(put_contracts)} out of {len(contracts_list)} are Puts")
    else:
        put_contracts = contracts_list
    
    if FILTER_DELTA:
        filtered = []
        for c in put_contracts:
            delta_raw = c.get("delta")
            if delta_raw is None or delta_raw == "":
                continue
            try:
                delta = float(delta_raw)
                if -0.50 <= delta <= -0.30:
                    filtered.append(c)
            except:
                pass
        logging.info(f"Delta filter: {len(filtered)} contracts with delta -0.50..-0.30")
        final_contracts = filtered
    else:
        final_contracts = put_contracts
    
    if not final_contracts:
        logging.warning("No contracts after filtering. CSV will have headers only.")
    
    headers = ["conid", "symbol", "right", "month", "strike", "maturityDate",
               "bid", "ask", "delta", "gamma", "theta", "vega"]
    filePath = "./DelayOptionContracts.csv"
    with open(filePath, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        for c in final_contracts:
            row = {h: c.get(h, "") for h in headers}
            writer.writerow(row)
    logging.info(f"✅ CSV saved to {filePath}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python script.py <TICKER> [NUMBER_OF_MONTHS]")
        sys.exit(1)
    ticker = sys.argv[1].upper()
    num_months = int(sys.argv[2]) if len(sys.argv) >= 3 else 4

    logging.info(f"Processing ticker: {ticker}, first {num_months} months as provided by API")
    logging.info(f"Delta filter: {'ON' if FILTER_DELTA else 'OFF'} (range -0.50 to -0.30)")

    try:
        underConid, months = secdefSearch(ticker)
    except Exception as e:
        logging.error(f"Failed to search for {ticker}: {e}")
        sys.exit(1)

    if not months:
        logging.error(f"No option months found for {ticker}")
        sys.exit(1)

    # Verwende die Monate in der Reihenfolge, wie von der API geliefert (ohne Sortierung)
    selected_months = months[:num_months]
    logging.info(f"Using months (as provided): {selected_months}")

    all_contracts = []
    for month in selected_months:
        logging.info(f"Processing {month}...")
        strikes = secdefStrikes(underConid, month)
        for strike in strikes:
            contracts = secdefInfo(underConid, month, strike, right="P")
            for c in contracts:
                c["month"] = month
                all_contracts.append(c)

    logging.info(f"Total contracts fetched: {len(all_contracts)}")
    writeResult(all_contracts)