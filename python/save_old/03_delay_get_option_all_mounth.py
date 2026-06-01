# FOUND FROM HEERE
# https://www.interactivebrokers.com/campus/ibkr-quant-news/handling-options-chains/

import requests
import urllib3
import csv
import json
import pprint

# Ignore insecure error messages
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def secdefSearch(symbol, listingExchange):
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
    """
    Ruft ALLE verfügbaren Strike-Preise für Puts ab (keine Preis-Filterung mehr).
    """
    url = f'https://localhost:4002/v1/api/iserver/secdef/strikes?conid={underConid}&secType=OPT&month={month}'
    strike_request = requests.get(url=url, verify=False)
    # Nur Puts – falls du auch Calls willst, ersetze "put" durch "call" oder kombiniere beide
    strikes = strike_request.json().get("put", [])
    # Keine Filterung mehr: alle Strikes werden zurückgegeben
    return strikes


def secdefInfo(conid, month, strike):
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


def snapshotData(underConid):
    """Holt verzögerten Marktpreis des Basiswerts (Parameter &delay=1)"""
    url = f'https://localhost:4002/v1/api/iserver/marketdata/snapshot?conids={underConid}&delay=1&snapshot=1'
    snapshot = requests.get(url=url, verify=False)
    data = snapshot.json()[0]
    return data.get("31", 0)


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


def writeResult(contractDict):
    all_contracts = []
    conid_to_contract = {}
    for strikeGroup in contractDict:
        for contractDetails in contractDict[strikeGroup]:
            all_contracts.append(contractDetails)
            conid_to_contract[contractDetails["conid"]] = contractDetails

    all_conids = [c["conid"] for c in all_contracts]
    snapshot_data = get_option_snapshot_bulk(all_conids)

    for conid, quote in snapshot_data.items():
        if conid in conid_to_contract:
            contract = conid_to_contract[conid]
            contract.update(quote)

    headers = ["conid", "symbol", "strike", "maturityDate",
               "bid", "ask", "delta", "gamma", "theta", "vega"]
    filePath = "./DelayOptionContracts.csv"
    with open(filePath, 'w', newline='') as contract_csv_file:
        contract_writer = csv.DictWriter(f=contract_csv_file, fieldnames=headers)
        contract_writer.writeheader()
        for contract in all_contracts:
            for key in headers:
                if key not in contract:
                    contract[key] = ""
            contract_writer.writerow(contract)
    print("Job's done. CSV mit verzögerten Bid/Ask und Greeks erstellt.")


if __name__ == "__main__":
    underConid, months = secdefSearch("AAPL", "NASDAQ")
    month = months[0]                     # Front Month
    all_strikes = secdefStrikes(underConid, month)   # Jetzt ALLE Strikes

    contractDict = {}
    for strike in all_strikes:
        contractDict[strike] = secdefInfo(underConid, month, strike)

    writeResult(contractDict)