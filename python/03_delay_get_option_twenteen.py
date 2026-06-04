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

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s : %(lineno)d - %(message)s')
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

PREFERRED_EXCHANGES = ["NASDAQ", "NYSE", "NYSE MKT", "BATS", "SMART", "AMEX"]
FILTER_DELTA = False          # True = nur Deltas -0.50..-0.30, False = alle
FORCE_PUT_ONLY = True        # Entfernt alle Nicht-PUTs aus der CSV

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

def get_stock_price(conid, symbol):
    """
    Ruft den aktuellen Aktienkurs (Underlying) über die REST-API (Methode 1) ab.
    Verwendet den Endpunkt /iserver/marketdata/snapshot.
    """
    logging.info(f"Fetching current stock price for {symbol} (conid={conid})...")
    
    authenticate_market_data()
    
    url = "https://localhost:4002/v1/api/iserver/marketdata/snapshot"
    params = {
        "conids": conid,
        "fields": "31,84,86"   # 31 = last, 84 = bid, 86 = ask
    }
    try:
        resp = requests.get(url, params=params, verify=False)
        resp.raise_for_status()
        data = resp.json()
        if data and isinstance(data, list) and len(data) > 0:
            item = data[0]
            last = item.get("31", "N/A")
            bid = item.get("84", "N/A")
            ask = item.get("86", "N/A")
            logging.info(f"✅ {symbol} - Last: {last}, Bid: {bid}, Ask: {ask}")
            return {
                "symbol": symbol,
                "conid": conid,
                "last": last,
                "bid": bid,
                "ask": ask,
                "timestamp": datetime.now().isoformat()
            }
        else:
            logging.warning(f"Unexpected response structure: {data}")
            return None
    except Exception as e:
        logging.error(f"Failed to fetch stock price for {symbol}: {e}")
        return None

def secdefSearch(symbol):
    url = f'https://localhost:4002/v1/api/iserver/secdef/search?symbol={symbol}'
    try:
        search_request = requests.get(url=url, verify=False)
        search_request.raise_for_status()
        data = search_request.json()
    except Exception as e:
        raise ValueError(f"API request failed for {symbol}: {e}")

    if not isinstance(data, list):
        raise ValueError(f"Unexpected API response for {symbol}: {data}")

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
        raise ValueError(f"No option contract found for {symbol}")

    underConid = selected_contract.get("conid")
    if not underConid:
        raise ValueError(f"No conid for {symbol}")

    months = []
    for secType in selected_contract.get("sections", []):
        if secType.get("secType") == "OPT":
            months_str = secType.get("months", "")
            if months_str:
                months = months_str.split(';')
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

def get_option_snapshot_bulk(conids, fields="84,85,86,87,88,89", generic_ticks="100,101,104,106", max_attempts=2, delay=2, batch_size=10):
    """
    Abruf von Marktdaten im Streaming-Modus (snapshot=0), um generische Tick-Typen zu erhalten.
    - fields: Bid (84), Ask (85), Delta (86), Gamma (87), Theta (88), Vega (89)
    - generic_ticks: Option Volume (100), Open Interest (101), Historical Volatility (104), Implied Volatility (106)
    """
    if not conids:
        return {}
    authenticate_market_data()

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

    if isinstance(conids, int):
        print(f"Success: {conids} is a valid integer count.")
        # nur ein wert KEINE LISTE
        total_batches = 1
        len_conids=1
    else:
        print(f"Error: {conids} must be a whole number.")
        total_batches = (len(conids) + batch_size - 1) // batch_size
        len_conids=len(conids)

    for i in range(0, len_conids, batch_size):
        batch = conids[i:i+batch_size]
        batch_num = i//batch_size + 1
        logging.info(f"Batch {batch_num}/{total_batches} ({len(batch)} contracts)")

        conid_str = ",".join(str(c) for c in batch)
        # snapshot=0 für Streaming-Modus (ermöglicht generische Ticks)
        url = f'https://localhost:4002/v1/api/iserver/marketdata/snapshot?conids={conid_str}&fields={fields}&genericTickList={generic_ticks}&snapshot=0'
        logging.info(f"url mkt_date =>  {url}")
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

                    # Standard-Felder
                    for f_id, f_name in field_map.items():
                        val = item.get(f_id)
                        if val is not None:
                            batch_data[conid][f_name] = val
                            logging.info(f"f_id => {f_id}")
                            logging.info(f" VAL => {f_id} {f_name} val =>  {val}")
                        else:
                            batch_data[conid][f_name] = ""

                    # Generische Ticks aus der ersten Antwort
                    for g_id, g_name in generic_map.items():
                        val = item.get(g_id)
                        if val is not None:
                            batch_data[conid][g_name] = val
                        else:
                            batch_data[conid][g_name] = ""

                # Warte kurz, dann zweite Anfrage für vollständige generische Daten
                if attempt == 0:
                    time.sleep(1)
                    resp2 = requests.get(url=url, verify=False)
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

                # Prüfe Vollständigkeit (mindestens Basis-Felder)
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
                else:
                    logging.warning(f"Batch {batch_num} failed after {max_attempts} attempts")

        # Formatiere die Daten für diesen Batch
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

def writeResult(filtered_contracts):
    """
    Erwartet bereits gefilterte Kontrakte (z.B. die nächsten 10 Strikes unter dem Kurs).
    Führt nur noch den Marktdatenabruf und das Schreiben der CSV durch.
    """
    if not filtered_contracts:
        logging.warning("No contracts to process. CSV will be empty.")
        # Leere CSV mit Kopfzeile erstellen
        headers = ["conid", "symbol", "right", "month", "strike", "maturityDate",
                   "bid", "ask", "delta", "gamma", "theta", "vega",
                   "volume", "open_interest", "historical_volatility", "implied_volatility"]
        filePath = "./DelayOptionContracts.csv"
        with open(filePath, 'w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=headers)
            writer.writeheader()
        logging.info(f"✅ Empty CSV saved to {filePath}")
        return

    conid_to_contract = {c["conid"]: c for c in filtered_contracts}
    all_conids = list(conid_to_contract.keys())
    logging.info(f"Fetching market data for {len(all_conids)} contracts...")
    snapshot_data = get_option_snapshot_bulk(all_conids)

    for conid, quote in snapshot_data.items():
        if conid in conid_to_contract:
            conid_to_contract[conid].update(quote)

    # Vorzeichenkorrektur für Put-Optionen (falls API positive Werte liefert)
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

    write_debug_log(filtered_contracts)

    logging.info("DEBUG: First 10 deltas (raw):")
    for i, c in enumerate(filtered_contracts[:10]):
        logging.info(f"  {i+1}: conid={c.get('conid')}, delta={c.get('delta')}, right={c.get('right')}")

    # Da wir bereits Puts haben (FORCE_PUT_ONLY wurde beim Sammeln schon angewendet),
    # und Delta-Filter deaktiviert ist (oder wir ignorieren ihn hier),
    # schreiben wir direkt die gefilterten Kontrakte.
    headers = ["conid", "symbol", "right", "month", "strike", "maturityDate",
               "bid", "ask", "delta", "gamma", "theta", "vega",
               "volume", "open_interest", "historical_volatility", "implied_volatility"]
    filePath = "./DelayOptionContracts.csv"
    with open(filePath, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        for c in filtered_contracts:
            row = {h: c.get(h, "") for h in headers}
            writer.writerow(row)
    logging.info(f"✅ Options CSV saved to {filePath}")

def save_stock_price_to_csv(stock_data):
    """Speichert den Aktienkurs in einer eigenen CSV-Datei."""
    if not stock_data:
        logging.warning("No stock data to save.")
        return
    filePath = "./stock_price.csv"
    file_exists = False
    try:
        with open(filePath, 'r') as f:
            file_exists = True
    except FileNotFoundError:
        pass
    
    with open(filePath, 'a', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=["timestamp", "symbol", "conid", "last", "bid", "ask"])
        if not file_exists:
            writer.writeheader()
        writer.writerow(stock_data)
    logging.info(f"✅ Stock price appended to {filePath}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python script.py <TICKER> [NUMBER_OF_MONTHS]")
        sys.exit(1)
    ticker = sys.argv[1].upper()
    num_months = int(sys.argv[2]) if len(sys.argv) >= 3 else 4

    logging.info(f"Processing ticker: {ticker}, next {num_months} months")
    logging.info(f"Delta filter: {'ON' if FILTER_DELTA else 'OFF'} (range -0.50 to -0.30)")

    try:
        underConid, months = secdefSearch(ticker)
    except Exception as e:
        logging.error(f"Failed to search for {ticker}: {e}")
        sys.exit(1)

    # ----- AKTUELLEN AKTIENKURS ABRUFEN UND SPEICHERN -----
    stock_info = get_stock_price(underConid, ticker)
    if stock_info:
        save_stock_price_to_csv(stock_info)
        try:
            current_stock_price_float = float(stock_info['last'])
            logging.info(f"Aktueller Aktienkurs {ticker}: {current_stock_price_float}")
        except (ValueError, TypeError):
            logging.error("Kurs konnte nicht in float konvertiert werden. Filterung nach Strike nicht möglich.")
            sys.exit(1)
    else:
        logging.error("Kein Aktienkurs erhalten. Breche ab.")
        sys.exit(1)

    if not months:
        logging.error(f"No option months found for {ticker}")
        sys.exit(1)

    selected_months = months[:num_months]
    logging.info(f"Selected months: {selected_months}")

    # Alle Put-Kontrakte sammeln (ohne Marktdaten)
    all_contracts = []
    for month in selected_months:
        logging.info(f"Processing {month}...")
        strikes = secdefStrikes(underConid, month)
        strikes.reverse()
        for strike in strikes:
        #######
            # runden auf ganze dollar
            strike = float(strike)
            logging.info(f"Strike=>{strike}")
            logging.info(f" Strike {strike} Price {current_stock_price_float}")
            if strike > current_stock_price_float:
                logging.info("continue")
                continue
            contracts = secdefInfo(underConid, month, strike, right="P")
            # ATTENTION contract of month with possibly weekly
            for c in contracts:
                c["month"] = month
                all_contracts.append(c)
                conids=c["conid"]
                # aufruf nur mit einer option chain
                snapshot_data = get_option_snapshot_bulk(conids)

    logging.info(f"Total contracts fetched (before filtering by strike): {len(all_contracts)}")

    # ----- FILTER: NUR DIE 10 NÄCHSTEN STRIKES UNTER DEM AKTIENKURS -----
    lower_strikes = []
    for contract in all_contracts:
        try:
            strike = float(contract.get("strike", 0))
            if strike < current_stock_price_float:
                lower_strikes.append(contract)
        except (ValueError, TypeError):
            logging.warning(f"Ungültiger Strike für Contract {contract.get('conid')}: {contract.get('strike')}")

    # Sortieren absteigend (höchste Strikes unter dem Kurs zuerst)
    lower_strikes.sort(key=lambda x: float(x.get("strike", 0)), reverse=True)
    
    # Die ersten 10 nehmen
    top_10_underlying = lower_strikes[:10]
    logging.info(f"Nach Filter: {len(top_10_underlying)} Contracts (max 10) mit Strike < {current_stock_price_float}")

    if not top_10_underlying:
        logging.warning("Keine Kontrakte mit Strike unter dem Aktienkurs gefunden. CSV wird nur Kopfzeile enthalten.")

    # ----- MARKTDATEN FÜR GEFILTERTE KONTRAKTE ABRUFEN UND CSV SCHREIBEN -----
    writeResult(top_10_underlying)