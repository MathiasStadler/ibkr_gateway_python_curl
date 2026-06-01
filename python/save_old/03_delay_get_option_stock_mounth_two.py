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

# Configure logging to see what's happening
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Ignore insecure error messages
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

def authenticate_market_data():
    """Initialize market data session by calling /iserver/accounts."""
    logging.info("Checking authentication for market data...")
    url = "https://localhost:4002/v1/api/iserver/accounts"
    try:
        resp = requests.get(url=url, verify=False)
        resp.raise_for_status()
        logging.info("✅ Market data session initialized.")
        return True
    except Exception as e:
        logging.error(f"❌ Failed to initialize market data session: {e}")
        return False

def secdefSearch(symbol, listingExchange):
    """Search for the underlying asset and fetch all available option months."""
    url = f'https://localhost:4002/v1/api/iserver/secdef/search?symbol={symbol}'
    search_request = requests.get(url=url, verify=False)
    pprint.pprint(search_request.json())
    json_formatted_str = json.dumps(search_request.json(), indent=0)
    print(json_formatted_str)
    
    for contract in search_request.json():
        if contract["description"] == listingExchange:
            underConid = contract["conid"]
            for secType in contract["sections"]:
                 if secType["secType"] == "OPT":
                    months = secType["months"].split(';')
    return underConid, months

def secdefStrikes(underConid, month):
    """Fetch ALL strike prices for Puts for a given month."""
    url = f'https://localhost:4002/v1/api/iserver/secdef/strikes?conid={underConid}&secType=OPT&month={month}'
    strike_request = requests.get(url=url, verify=False)
    strikes = strike_request.json().get("put", [])
    logging.info(f"Fetched {len(strikes)} strikes for month {month}")
    return strikes

def secdefInfo(conid, month, strike):
    """Fetch contract details for a specific Put option."""
    url = f'https://localhost:4002/v1/api/iserver/secdef/info?conid={conid}&month={month}&strike={strike}&secType=OPT&right=P'
    info_request = requests.get(url=url, verify=False)
    contracts = []
    for contract in info_request.json():
        contractDetails = {
            "conid": contract["conid"],
            "symbol": contract["symbol"],
            "strike": contract["strike"],
            "maturityDate": contract["maturityDate"]
        }
        contracts.append(contractDetails)
    return contracts

def get_option_snapshot_bulk(conids, fields="84,85,86,87,88,89", max_attempts=3, delay=2):
    """
    Fetch delayed market data snapshot for a list of conids with retry logic.
    
    Args:
        conids: List of contract IDs
        fields: Comma-separated field IDs (84=Bid, 85=Ask, 86=Delta, etc.)
        max_attempts: Number of attempts to get all fields
        delay: Seconds to wait between attempts
    """
    if not conids:
        return {}
    
    # Authenticate first
    authenticate_market_data()
    
    conid_str = ",".join(str(c) for c in conids)
    url = f'https://localhost:4002/v1/api/iserver/marketdata/snapshot?conids={conid_str}&fields={fields}&delay=1&snapshot=1'
    
    field_map = {
        "84": "bid",
        "85": "ask",
        "86": "delta",
        "87": "gamma",
        "88": "theta",
        "89": "vega"
    }
    
    all_data = {}
    
    for attempt in range(max_attempts):
        try:
            resp = requests.get(url=url, verify=False)
            resp.raise_for_status()
            data = resp.json()
            
            # Store the data, merging if we already have some
            for item in data:
                conid = item.get("conid")
                if not conid:
                    continue
                
                if conid not in all_data:
                    all_data[conid] = {}
                
                # Update with any new fields we found
                for f_id, f_name in field_map.items():
                    value = item.get(f_id)
                    if value is not None:
                        all_data[conid][f_name] = value
            
            # Check if we have enough data
            complete_contracts = sum(1 for conid in all_data 
                                   if all(field in all_data[conid] for field in field_map.values()))
            total_contracts = len(conids)
            percentage = (complete_contracts / total_contracts) * 100 if total_contracts > 0 else 0
            
            logging.info(f"Attempt {attempt + 1}: Got complete data for {complete_contracts}/{total_contracts} contracts ({percentage:.1f}%)")
            
            if complete_contracts == total_contracts:
                logging.info("All fields received successfully!")
                break
                
            # Wait before next attempt
            if attempt < max_attempts - 1:
                wait_time = delay * (attempt + 1)  # Increase wait time with each attempt
                logging.info(f"Waiting {wait_time} seconds before next attempt...")
                time.sleep(wait_time)
                
        except Exception as e:
            logging.error(f"Attempt {attempt + 1} failed: {e}")
            if attempt < max_attempts - 1:
                time.sleep(delay)
            else:
                logging.error("Max attempts reached, continuing with partial data.")
    
    # Convert to the expected format (bid/ask as strings, others as numbers)
    result = {}
    for conid, quote in all_data.items():
        formatted_quote = {}
        for f_name in field_map.values():
            value = quote.get(f_name)
            # Keep numeric fields as numbers, but bid/ask as strings (they may be empty)
            if f_name in ["bid", "ask"]:
                formatted_quote[f_name] = str(value) if value is not None else ""
            else:
                formatted_quote[f_name] = value if value is not None else ""
        result[conid] = formatted_quote
    
    return result

def writeResult(contracts_list):
    """Write the list of contract dictionaries to a CSV file."""
    # Map conid -> contract for easy access
    conid_to_contract = {c["conid"]: c for c in contracts_list}
    all_conids = list(conid_to_contract.keys())
    
    logging.info(f"Fetching market data for {len(all_conids)} contracts...")
    
    # Fetch market data with automatic retries
    snapshot_data = get_option_snapshot_bulk(all_conids)
    
    # Update contracts with market data
    for conid, quote in snapshot_data.items():
        if conid in conid_to_contract:
            conid_to_contract[conid].update(quote)
    
    # Headers including the month column
    headers = ["conid", "symbol", "month", "strike", "maturityDate",
               "bid", "ask", "delta", "gamma", "theta", "vega"]
    
    filePath = "./DelayOptionContracts.csv"
    with open(filePath, 'w', newline='') as contract_csv_file:
        contract_writer = csv.DictWriter(f=contract_csv_file, fieldnames=headers)
        contract_writer.writeheader()
        
        for contract in contracts_list:
            # Fill missing fields with empty strings
            for key in headers:
                if key not in contract:
                    contract[key] = ""
            contract_writer.writerow(contract)
    
    logging.info(f"✅ Done! CSV saved to {filePath}")

if __name__ == "__main__":
    # Validate ticker parameter
    if len(sys.argv) < 2:
        print("❌ Error: Please specify a ticker.")
        print("Usage: python script.py <TICKER> [NUMBER_OF_MONTHS]")
        print("Example: python script.py AAPL 6")
        sys.exit(1)
    
    ticker = sys.argv[1].upper()
    
    # Number of months (default 4)
    if len(sys.argv) >= 3:
        try:
            num_months = int(sys.argv[2])
        except ValueError:
            print("❌ Error: Number of months must be an integer.")
            sys.exit(1)
    else:
        num_months = 4
    
    logging.info(f"Processing ticker: {ticker}, next {num_months} months")
    
    # Fetch underlying and available months (using NASDAQ exchange)
    try:
        underConid, months = secdefSearch(ticker, "NASDAQ")
    except Exception as e:
        logging.error(f"Failed to search for {ticker}: {e}")
        sys.exit(1)
    
    if not months:
        logging.error(f"No option months found for {ticker}")
        sys.exit(1)
    
    # Sort months and take the first 'num_months'
    sorted_months = sorted(months)[:num_months]
    logging.info(f"Processing months: {sorted_months}")
    
    all_contracts = []
    
    for month in sorted_months:
        logging.info(f"\n--- Processing month: {month} ---")
        strikes = secdefStrikes(underConid, month)
        logging.info(f"Found {len(strikes)} strikes (Puts)")
        
        for strike in strikes:
            contracts = secdefInfo(underConid, month, strike)
            for contract in contracts:
                contract["month"] = month
                all_contracts.append(contract)
    
    logging.info(f"\nTotal options fetched: {len(all_contracts)}")
    writeResult(all_contracts)