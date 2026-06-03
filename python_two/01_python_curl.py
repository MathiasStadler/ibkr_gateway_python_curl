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
FILTER_DELTA = False
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

def get_stock_price(conid, symbol):
    logging.info(f"Fetching current stock price for {symbol} (conid={conid})...")
    authenticate_market_data()
    url = "https://localhost:4002/v1/api/iserver/marketdata/snapshot"
    params = {"conids": conid, "fields": "31,84,86"}
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
            logging.warning(f"Unexpected response: {data}")
            return None
    except Exception as e:
        logging.error(f"Failed to fetch stock price: {e}")
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

def get_option_snapshot_bulk(conids):
    """
    Ruft Marktdaten für eine Liste von conids ab.
    Verwendet snapshot=1 (Schnappschuss) mit allen gewünschten Feldern.
    """
    if not conids:
        return {}
    authenticate_market_data()

    fields = "84,85,86,87,88,89"
    generic_ticks = "100,101,104,106"
    
    all_data = {}
    batch_size = 10
    for i in range(0, len(conids), batch_size):
        batch = conids[i:i+batch_size]
        conid_str = ",".join(str(c) for c in batch)
        url = f'https://localhost:4002/v1/api/iserver/marketdata/snapshot?conids={conid_str}&fields={fields}&genericTickList={generic_ticks}&snapshot=1'
        logging.info(f"Requesting market data for batch of {len(batch)} contracts")
        try:
            resp = requests.get(url, verify=False)
            resp.raise_for_status()
            data = resp.json()
            for item in data:
                cid = item.get("conid")
                if not cid:
                    continue
                quote = {
                    "bid": item.get("84", ""),
                    "ask": item.get("85", ""),
                    "delta": item.get("86", ""),
                    "gamma": item.get("87", ""),
                    "theta": item.get("88", ""),
                    "vega": item.get("89", ""),
                    "volume": item.get("100", ""),
                    "open_interest": item.get("101", ""),
                    "historical_volatility": item.get("104", ""),
                    "implied_volatility": item.get("106", "")
                }
                all_data[cid] = quote
            logging.info(f"Received data for {len(data)} contracts")
        except Exception as e:
            logging.error(f"Failed to fetch market data for batch: {e}")
        time.sleep(0.5)
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
    """Ruft Marktdaten ab, reichert die Kontrakte an und schreibt CSV."""
    if not contracts_list:
        logging.warning("No contracts to process. CSV will be empty.")
        headers = ["conid", "symbol", "right", "month", "strike", "maturityDate",
                   "bid", "ask", "delta", "gamma", "theta", "vega",
                   "volume", "open_interest", "historical_volatility", "implied_volatility"]
        with open("./DelayOptionContracts.csv", 'w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=headers)
            writer.writeheader()
        return

    conid_to_contract = {c["conid"]: c for c in contracts_list}
    all_conids = list(conid_to_contract.keys())
    logging.info(f"Fetching market data for {len(all_conids)} contracts...")
    snapshot_data = get_option_snapshot_bulk(all_conids)

    for conid, quote in snapshot_data.items():
        if conid in conid_to_contract:
            conid_to_contract[conid].update(quote)

    # Vorzeichenkorrektur für Puts (Delta, Gamma)
    for contract in contracts_list:
        if contract.get("right") == "P":
            if "delta" in contract and contract["delta"]:
                try:
                    d = float(contract["delta"])
                    if 0 <= d <= 1:
                        contract["delta"] = -d
                except:
                    pass
            if "gamma" in contract and contract["gamma"]:
                try:
                    g = float(contract["gamma"])
                    if g > 0:
                        contract["gamma"] = -g
                except:
                    pass

    write_debug_log(contracts_list)

    headers = ["conid", "symbol", "right", "month", "strike", "maturityDate",
               "bid", "ask", "delta", "gamma", "theta", "vega",
               "volume", "open_interest", "historical_volatility", "implied_volatility"]
    filePath = "./DelayOptionContracts.csv"
    with open(filePath, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        for c in contracts_list:
            row = {h: c.get(h, "") for h in headers}
            writer.writerow(row)
    logging.info(f"✅ Options CSV saved to {filePath}")

def save_stock_price_to_csv(stock_data):
    if not stock_data:
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

    try:
        underConid, months = secdefSearch(ticker)
    except Exception as e:
        logging.error(f"Failed to search for {ticker}: {e}")
        sys.exit(1)

    # Aktienkurs abrufen
    stock_info = get_stock_price(underConid, ticker)
    if stock_info:
        save_stock_price_to_csv(stock_info)
        # Versuche, den Kurs als Float zu parsen – mit Fallback auf Bid/Ask
        try:
            last_str = stock_info.get('last', '')
            if last_str in ('', 'N/A', None):
                bid_str = stock_info.get('bid', '')
                ask_str = stock_info.get('ask', '')
                if bid_str not in ('', 'N/A', None) and ask_str not in ('', 'N/A', None):
                    current_price = (float(bid_str) + float(ask_str)) / 2.0
                    logging.info(f"Verwende Mittelwert aus Bid/Ask: {current_price}")
                else:
                    raise ValueError(f"Kein gültiger Last, Bid oder Ask: last={last_str}, bid={bid_str}, ask={ask_str}")
            else:
                current_price = float(last_str)
            logging.info(f"Aktueller Aktienkurs {ticker}: {current_price}")
        except Exception as e:
            logging.error(f"Kurs nicht konvertierbar: {e}")
            sys.exit(1)
    else:
        logging.error("Kein Aktienkurs erhalten")
        sys.exit(1)

    if not months:
        logging.error(f"No option months found for {ticker}")
        sys.exit(1)

    selected_months = months[:num_months]
    logging.info(f"Selected months: {selected_months}")

    # 15 nächste Strikes unter dem aktuellen Kurs
    candidate_strikes = []
    for month in selected_months:
        strikes = secdefStrikes(underConid, month)
        for strike in strikes:
            try:
                s = float(strike)
                if s < current_price:
                    candidate_strikes.append((month, s))
            except:
                pass

    if not candidate_strikes:
        logging.warning("Keine Strikes unter dem aktuellen Kurs gefunden.")
        all_contracts = []
    else:
        candidate_strikes.sort(key=lambda x: x[1], reverse=True)
        top_15 = candidate_strikes[:15]
        logging.info(f"Selected {len(top_15)} strikes (max 15) below {current_price}")

        all_contracts = []
        for month, strike in top_15:
            contracts = secdefInfo(underConid, month, strike, right="P")
            for c in contracts:
                c["month"] = month
                all_contracts.append(c)

    logging.info(f"Total contracts fetched: {len(all_contracts)}")
    writeResult(all_contracts)