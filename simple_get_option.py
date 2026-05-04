import requests
import urllib3
import time

# Diese Warnung kann ignoriert werden, da wir das Gateway mit einem selbstsignierten Zertifikat verwenden.
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# --- 1. Konfiguration ---
BASE_URL = "https://127.0.0.1:4002/v1/api"
SYMBOL = "PLTR"
EXCHANGE = "NASDAQ"      # Die Börse, an der der Basiswert notiert ist
MONTH_COUNT = 2          # Wie viele der nächsten Verfallsmonate durchlaufen werden sollen
MAX_RESULTS = 5          # Nur zu Testzwecken: Nur die ersten 5 Optionen anzeigen

# --- 2. Hilfsfunktion für API-Aufrufe ---
def call_api(endpoint, params=None):
    """Führt einen GET-Request an die IBKR API durch."""
    url = f"{BASE_URL}{endpoint}"
    try:
        # 'verify=False' ist nötig für das selbstsignierte Zertifikat des Gateways
        response = requests.get(url, params=params, verify=False, timeout=10)
        response.raise_for_status()  # Löst eine Exception bei HTTP-Fehlern aus
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"  ❌ API-Fehler bei {endpoint}: {e}")
        return None

# --- 3. Schritt 1: Den Basiswert (Underlying) finden ---
print(f"🔍 Schritt 1: Suche nach dem Basiswert '{SYMBOL}' an der Börse '{EXCHANGE}'...")
search_params = {"symbol": SYMBOL}
search_results = call_api("/iserver/secdef/search", search_params)
if not search_results:
    raise Exception("Konnte keine Daten von der API erhalten. Ist das Gateway gestartet und eingeloggt?")

# Finde den korrekten Vertrag anhand der Börse
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
    raise Exception(f"Konnte keinen Vertrag für {SYMBOL} an der Börse {EXCHANGE} finden.")
print(f"  ✅ Basiswert gefunden. Conid: {underlying_conid}")
print(f"  📅 Verfügbare Optionsmonate: {available_months}")

# --- 4. Schritt 2: Strikes für einen Optionsmonat abrufen ---
# Wir beschränken uns auf die ersten 'MONTH_COUNT' Monate
for month in available_months[:MONTH_COUNT]:
    print(f"\n🌙 Verarbeite Optionsmonat: {month}")
    strikes_params = {
        "conid": underlying_conid,
        "sectype": "OPT",   # 'OPT' steht für Optionen
        "month": month,
        "exchange": "SMART"
    }
    strikes_data = call_api("/iserver/secdef/strikes", strikes_params)
    if not strikes_data:
        print(f"  ⚠️ Keine Strikes für diesen Monat gefunden.")
        continue

    # Wir holen uns die Liste der Put-Strikes (für Calls wäre es 'call')
    all_strikes = strikes_data.get("put", [])
    if not all_strikes:
        print(f"  ⚠️ Keine Put-Strikes für diesen Monat gefunden.")
        continue
    print(f"  📊 {len(all_strikes)} Put-Strikes gefunden (z.B. {all_strikes[:3]}).")

    # --- 5. Schritt 3: Details für jeden Strike abrufen ---
    option_conids = []
    for strike in all_strikes:
        info_params = {
            "conid": underlying_conid,
            "sectype": "OPT",
            "month": month,
            "strike": strike,
            "right": "P"      # 'P' für Put, 'C' für Call
        }
        contract_info = call_api("/iserver/secdef/info", info_params)
        if contract_info and isinstance(contract_info, list) and contract_info:
            conid = contract_info[0].get("conid")
            if conid:
                option_conids.append(conid)

    if not option_conids:
        print(f"  ⚠️ Konnte für keine Option die Conid abrufen.")
        continue

    print(f"  🆔 {len(option_conids)} Conids für Optionen im Monat {month} abgerufen.")
    
    # --- 6. Schritt 4: Marktdaten für die Conids im Snapshot abrufen ---
    # Die API erlaubt maximal 100 Conids pro Anfrage. Wir teilen daher auf.
    batch_size = 100
    for i in range(0, len(option_conids), batch_size):
        batch = option_conids[i:i+batch_size]
        conids_param = ",".join(map(str, batch))
        # Die Felder: 7314 = Delta, 54 = Open Interest, 31 = Letzter Preis
        snapshot_params = {"conids": conids_param, "fields": "7314,54,31"}
        
        print(f"    📡 Fordere Marktdaten für Batch {i//batch_size + 1} an (Größe: {len(batch)})...")
        snapshot_data = call_api("/iserver/marketdata/snapshot", snapshot_params)
        
        if not snapshot_data or not isinstance(snapshot_data, list):
            print(f"    ⚠️ Keine Marktdaten für diesen Batch erhalten.")
            continue

        # Die API braucht einen Moment, um die Griechen wie Delta zu berechnen.
        # Daher folgt eine kurze Pause, bevor wir die Daten auswerten.
        time.sleep(1.5)
        
        # Eine zweite Anfrage für denselben Batch stellt sicher, dass Delta etc. berechnet sind.
        snapshot_data_retry = call_api("/iserver/marketdata/snapshot", snapshot_params)
        if snapshot_data_retry and isinstance(snapshot_data_retry, list):
            snapshot_data = snapshot_data_retry

        print(f"    🧾 Ergebnisse (erste {MAX_RESULTS} Optionen von {len(batch)}):")
        for idx, item in enumerate(snapshot_data[:MAX_RESULTS]):
            conid = item.get('conid')
            delta = item.get('7314', 'N/A')
            open_interest = item.get('54', 'N/A')
            last_price = item.get('31', 'N/A')
            print(f"      {idx+1}. Conid: {conid} | Δ: {delta:>6} | OI: {open_interest:>8} | Last: {last_price:>6}")

        # Wir brechen nach dem ersten erfolgreichen Batch ab, da es sich um einen Test handelt.
        break
    # Nach dem ersten Monat und Batch abbrechen, da es sich um einen Test handelt.
    break
print("\n✨ Test abgeschlossen.")