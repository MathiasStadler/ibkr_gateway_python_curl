# options_chain_delayed.py
# Ruft für AAPL Puts (Frontmonat, Strikes ±10 $) folgende Daten ab:
#   conid, symbol, strike, maturityDate, bid, ask, delta, gamma, theta, vega

import requests
import urllib3
import csv

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BASE_URL = "http://localhost:4002"          # Ihr Gateway läuft auf HTTP Port 4002
HEADERS = {"User-Agent": "Mozilla/5.0"}

# ---------- 1. Hilfsfunktionen für die Kontrakt- und Stripe-Suche ----------
def secdefSearch(symbol, listingExchange):
    url = f"{BASE_URL}/v1/api/iserver/secdef/search?symbol={symbol}"
    resp = requests.get(url, headers=HEADERS, verify=False)
    resp.raise_for_status()
    for contract in resp.json():
        if contract["description"] == listingExchange:
            underConid = contract["conid"]
            for secType in contract["sections"]:
                if secType["secType"] == "OPT":
                    months = secType["months"].split(";")
            return underConid, months
    raise ValueError(f"Exchange {listingExchange} not found")

def snapshotUnderlying(underConid):
    """Verzögerter Marktpreis des Basiswerts (Feld 31 = Last)"""
    url = f"{BASE_URL}/v1/api/iserver/marketdata/snapshot?conids={underConid}&delay=1&snapshot=1"
    resp = requests.get(url, headers=HEADERS, verify=False)
    resp.raise_for_status()
    data = resp.json()[0]
    return float(data.get("31", 0))

def secdefStrikes(underConid, month):
    underlying_price = snapshotUnderlying(underConid)
    itmStrikes = []
    url = f"{BASE_URL}/v1/api/iserver/secdef/strikes?conid={underConid}&secType=OPT&month={month}"
    resp = requests.get(url, headers=HEADERS, verify=False)
    resp.raise_for_status()
    strikes = resp.json()["put"]
    for strike in strikes:
        if abs(strike - underlying_price) <= 10.0:
            itmStrikes.append(strike)
    return itmStrikes

def secdefInfo(conid, month, strike):
    """Ruft die Kontrakt-Details für einen bestimmten Strike (Put) ab"""
    url = f"{BASE_URL}/v1/api/iserver/secdef/info?conid={conid}&month={month}&strike={strike}&secType=OPT&right=P"
    resp = requests.get(url, headers=HEADERS, verify=False)
    resp.raise_for_status()
    contracts = []
    for contract in resp.json():
        contractDetails = {
            "conid": contract["conid"],
            "symbol": contract["symbol"],
            "strike": contract["strike"],
            "maturityDate": contract["maturityDate"],
        }
        contracts.append(contractDetails)
    return contracts

# ---------- 2. Bulk-Marktdaten für Optionen (Bid, Ask, Greeks) ----------
def get_option_snapshot_bulk(conids, fields="84,85,86,87,88,89"):
    """
    fields: 84=Bid, 85=Ask, 86=Delta, 87=Gamma, 88=Theta, 89=Vega
    """
    if not conids:
        return {}
    conid_str = ",".join(str(c) for c in conids)
    url = f"{BASE_URL}/v1/api/iserver/marketdata/snapshot?conids={conid_str}&fields={fields}&delay=1&snapshot=1"
    resp = requests.get(url, headers=HEADERS, verify=False, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    field_map = {
        "84": "bid",
        "85": "ask",
        "86": "delta",
        "87": "gamma",
        "88": "theta",
        "89": "vega",
    }
    result = {}
    for item in data:
        conid = item.get("conid")
        if not conid:
            continue
        quote = {}
        for f_id, f_name in field_map.items():
            val = item.get(f_id)
            quote[f_name] = val if val is not None else ""
        result[conid] = quote
    return result

# ---------- 3. Ergebnis in CSV schreiben ----------
def writeResult(contractDict):
    all_contracts = []
    conid_to_contract = {}
    for strikeGroup in contractDict.values():
        for contract in strikeGroup:
            conid = contract["conid"]
            all_contracts.append(contract)
            conid_to_contract[conid] = contract

    # Bulk‑Snapshot für alle Optionen
    all_conids = [c["conid"] for c in all_contracts]
    snapshot_data = get_option_snapshot_bulk(all_conids)

    for conid, quote in snapshot_data.items():
        if conid in conid_to_contract:
            conid_to_contract[conid].update(quote)

    headers = ["conid", "symbol", "strike", "maturityDate",
               "bid", "ask", "delta", "gamma", "theta", "vega"]
    filePath = "./MayContracts.csv"
    with open(filePath, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        for contract in all_contracts:
            row = {h: contract.get(h, "") for h in headers}
            writer.writerow(row)
    print(f"✅ CSV erfolgreich geschrieben: {filePath}")

# ---------- 4. Hauptprogramm ----------
if __name__ == "__main__":
    print("🔍 Suche Apple (NASDAQ) Optionen ...")
    underConid, months = secdefSearch("AAPL", "NASDAQ")
    if not months:
        raise RuntimeError("Keine Optionsmonate gefunden")

    month = months[0]          # Front Month
    print(f"📅 Front Month: {month}")

    strikes = secdefStrikes(underConid, month)
    if not strikes:
        print("⚠️ Keine Strikes im Bereich ±10 $ gefunden.")
        exit(0)
    print(f"🎯 Strikes: {strikes}")

    contractDict = {}
    for strike in strikes:
        contractDict[strike] = secdefInfo(underConid, month, strike)

    writeResult(contractDict)