# FOUND FROM HEERE
# https://www.interactivebrokers.com/campus/ibkr-quant-news/handling-options-chains/

import sys
import requests
import urllib3
import csv
import json
import pprint

# Ignore insecure error messages
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def secdefSearch(symbol, listingExchange):
    """Suche nach dem Underlying und hole alle verfügbaren Options-Monate."""
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
    """Hole ALLE Strike-Preise für Puts eines bestimmten Monats."""
    url = f'https://localhost:4002/v1/api/iserver/secdef/strikes?conid={underConid}&secType=OPT&month={month}'
    strike_request = requests.get(url=url, verify=False)
    strikes = strike_request.json().get("put", [])
    return strikes  # keine Filterung mehr


def secdefInfo(conid, month, strike):
    """Hole die Vertragsdetails für einen bestimmten Put (Strike, Monat)."""
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


def get_option_snapshot_bulk(conids, fields="84,85,86,87,88,89"):
    """
    Ruft für eine Liste von conids die verzögerten Marktdaten ab.
    fields: 84=Bid, 85=Ask, 86=Delta, 87=Gamma, 88=Theta, 89=Vega
    """
    if not conids:
        return {}
    conid_str = ",".join(str(c) for c in conids)
    url = f'https://localhost:4002/v1/api/iserver/marketdata/snapshot?conids={conid_str}&fields={fields}&delay=1&snapshot=1'
    try:
        resp = requests.get(url=url, verify=False)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"Fehler beim Snapshot: {e}")
        return {}

    result = {}
    field_map = {
        "84": "bid",
        "85": "ask",
        "86": "delta",
        "87": "gamma",
        "88": "theta",
        "89": "vega"
    }
    for item in data:
        conid = item.get("conid")
        if not conid:
            continue
        quote = {}
        for f_id, f_name in field_map.items():
            value = item.get(f_id)
            quote[f_name] = value if value is not None else ""
        result[conid] = quote
    return result


def writeResult(contracts_list):
    """Schreibt eine Liste von Vertragsdictionaries in eine CSV-Datei."""
    # Abbildung von conid -> Contract für schnellen Zugriff
    conid_to_contract = {c["conid"]: c for c in contracts_list}
    all_conids = list(conid_to_contract.keys())
    
    # Marktdaten (Bid, Ask, Griechen) für alle Verträge holen
    snapshot_data = get_option_snapshot_bulk(all_conids)
    
    for conid, quote in snapshot_data.items():
        if conid in conid_to_contract:
            conid_to_contract[conid].update(quote)
    
    # Feld 'month' wurde hinzugefügt
    headers = ["conid", "symbol", "month", "strike", "maturityDate",
               "bid", "ask", "delta", "gamma", "theta", "vega"]
    filePath = "./DelayOptionContracts.csv"
    with open(filePath, 'w', newline='') as contract_csv_file:
        contract_writer = csv.DictWriter(f=contract_csv_file, fieldnames=headers)
        contract_writer.writeheader()
        for contract in contracts_list:
            # Fehlende Felder mit leerem String auffüllen
            for key in headers:
                if key not in contract:
                    contract[key] = ""
            contract_writer.writerow(contract)
    print("Job's done. CSV mit verzögerten Bid/Ask und Greeks erstellt.")


if __name__ == "__main__":
    # Prüfe Ticker-Parameter
    if len(sys.argv) < 2:
        print("Fehler: Bitte geben Sie einen Ticker an.")
        print("Aufruf: python script.py <TICKER> [ANZAHL_MONATE]")
        print("Beispiel: python script.py AAPL 6")
        sys.exit(1)
    
    ticker = sys.argv[1].upper()
    
    # Anzahl der Monate (default 4)
    if len(sys.argv) >= 3:
        try:
            num_months = int(sys.argv[2])
        except ValueError:
            print("Fehler: Die Monatsanzahl muss eine ganze Zahl sein.")
            sys.exit(1)
    else:
        num_months = 4
    
    print(f"Verarbeite Ticker: {ticker}, nächste {num_months} Monate")
    
    # Underlying und alle verfügbaren Monate holen (hier fest mit NASDAQ)
    try:
        underConid, months = secdefSearch(ticker, "NASDAQ")
    except Exception as e:
        print(f"Fehler bei der Suche nach {ticker}: {e}")
        sys.exit(1)
    
    if not months:
        print(f"Keine Optionsmonate für {ticker} gefunden.")
        sys.exit(1)
    
    # Monate sortieren (aufsteigend) und die ersten 'num_months' nehmen
    sorted_months = sorted(months)[:num_months]
    print(f"Verarbeite {len(sorted_months)} Monate: {sorted_months}")
    
    all_contracts = []
    
    for month in sorted_months:
        print(f"\n--- Monat {month} ---")
        strikes = secdefStrikes(underConid, month)
        print(f"Anzahl Strikes (Puts): {len(strikes)}")
        
        for strike in strikes:
            contracts = secdefInfo(underConid, month, strike)
            for contract in contracts:
                contract["month"] = month   # Monatsinformation hinzufügen
                all_contracts.append(contract)
    
    print(f"\nInsgesamt {len(all_contracts)} Optionen abgerufen.")
    writeResult(all_contracts)