import requests
import urllib3
import time

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# 1. Konfiguration
BASE_URL = "https://127.0.0.1:4002/v1/api"
SYMBOL = "PLTR"
EXCHANGE = "NASDAQ"
MONTH_COUNT = 1
MAX_RESULTS = 5

def call_api(endpoint, params=None):
    url = f"{BASE_URL}{endpoint}"
    try:
        response = requests.get(url, params=params, verify=False, timeout=10)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"  ❌ API-Fehler: {e}")
        return None

def get_market_data_with_delta(conids, delay=2.0):
    """Holt Marktdaten + Delta-Retry-Schleife."""
    all_data = {}
    for i in range(0, len(conids), 90):       # max. 100 conids
        batch = conids[i:i+90]
        conids_str = ",".join(map(str, batch))
        params = {"conids": conids_str, "fields": "31,54,7314"}  # 31=Last,54=OI,7314=Delta
        print(f"    📡 Batch-Anfrage für {len(batch)} Conids...")

        # Erster Versuch (initiiert den Datenstrom)
        data = call_api("/iserver/marketdata/snapshot", params)
        if not data or not isinstance(data, list):
            print("      ⚠️ Keine Daten im ersten Versuch.")
            continue

        # Zweiter Versuch nach kurzer Wartezeit (für die Berechnung der Greeks)
        print(f"      ⏳ Warte {delay} Sekunden für Delta-Berechnung...")
        time.sleep(delay)

        data_retry = call_api("/iserver/marketdata/snapshot", params)
        if data_retry and isinstance(data_retry, list):
            for entry in data_retry:
                conid = entry.get('conid')
                if conid:
                    all_data[conid] = {
                        "conid": conid,
                        "delta": entry.get("7314"),
                        "open_interest": entry.get("54"),
                        "last_price": entry.get("31")
                    }
        else:
            # Fallback: Daten des ersten Versuchs verwenden
            for entry in data:
                conid = entry.get('conid')
                if conid:
                    all_data[conid] = {
                        "conid": conid,
                        "delta": entry.get("7314"),
                        "open_interest": entry.get("54"),
                        "last_price": entry.get("31")
                    }

        # Kurze Pause zwischen den Batches
        time.sleep(0.5)
    return all_data

# 2. Underlying suchen
print(f"🔍 Suche Underlying für {SYMBOL}...")
search_results = call_api("/iserver/secdef/search", {"symbol": SYMBOL})
if not search_results:
    raise Exception("Gateway nicht erreichbar?")

underlying_conid = None
available_months = []
for contract in search_results:
    if contract.get("description") == EXCHANGE:
        underlying_conid = contract.get("conid")
        for section in contract.get("sections", []):
            if section.get("secType") == "OPT":
                months_str = section.get("months", "")
                if months_str:
                    available_months = months_str.split(';')
                break
        break

if not underlying_conid:
    raise Exception(f"Kein Vertrag für {SYMBOL} an {EXCHANGE} gefunden.")
print(f"  ✅ Underlying Conid: {underlying_conid}")
print(f"  📅 Monate: {available_months[:MONTH_COUNT+1]}")

# 3. Strikes & Optionen sammeln
all_option_conids = []
for month in available_months[:MONTH_COUNT]:
    print(f"\n🌙 Verarbeite Monat {month}...")
    strikes_data = call_api("/iserver/secdef/strikes", {
        "conid": underlying_conid,
        "sectype": "OPT",
        "month": month
    })
    if not strikes_data:
        continue

    for strike in strikes_data.get("put", []):    # nur Puts, wie gewünscht
        info = call_api("/iserver/secdef/info", {
            "conid": underlying_conid,
            "sectype": "OPT",
            "month": month,
            "strike": strike,
            "right": "P"
        })
        if info and isinstance(info, list) and info:
            conid = info[0].get("conid")
            if conid:
                all_option_conids.append(conid)

# 4. Delta-Daten mit Retry-Logik abrufen
print(f"\n📊 {len(all_option_conids)} Optionen gefunden, beginne Delta-Abruf...")
market_data = get_market_data_with_delta(all_option_conids, delay=2.0)

# 5. Ausgabe
print("\n=== Ergebnisse (mit Delta) ===")
count = 0
for conid, data in market_data.items():
    delta = data["delta"]
    if delta is None:
        continue               # Delta nicht verfügbar (z.B. bei illiquiden Strikes)
    print(f"Conid: {conid} | Δ: {delta:>6} | OI: {data['open_interest']:>8} | Last: {data['last_price']:>6}")
    count += 1
    if count >= MAX_RESULTS:
        break

if count == 0:
    print("⚠️ Keine Delta-Werte verfügbar. Die Optionen könnten illiquide sein.")
print("\n✨ Fertig.")