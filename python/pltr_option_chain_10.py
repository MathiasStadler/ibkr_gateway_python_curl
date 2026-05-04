import requests
import urllib3
import csv
import time
import json
from datetime import datetime, timedelta

# Ignore insecure error messages (Gateway nutzt selbstsignierte Zertifikate)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ------------------------------------------------------------
# 1. Konfiguration
# ------------------------------------------------------------
BASE_URL = "https://127.0.0.1:4002/v1/api"
SYMBOL = "PLTR"
MAX_RESULTS = 30
DELTA_MIN = -0.50
DELTA_MAX = -0.25

# Kopfzeilen für die CSV-Ausgabe
CSV_HEADER = [
    "symbol", "conid", "strike", "right", "maturity_date",
    "delta", "open_interest", "last_price"
]

# ------------------------------------------------------------
# 2. Session-Verwaltung (Tickle-Endpunkt)
# ------------------------------------------------------------
def get_session_token():
    """
    Ruft den /tickle-Endpunkt auf, um ein gültiges Session-Cookie zu erhalten.
    Gibt die Session-ID als String zurück.
    """
    try:
        response = requests.get(
            f"{BASE_URL}/tickle",
            verify=False,
            timeout=10
        )
        response.raise_for_status()
        data = response.json()
        session_id = data.get("session")
        if not session_id:
            raise Exception("Keine Session-ID in der Antwort gefunden.")
        return session_id
    except Exception as e:
        raise Exception(f"Fehler beim Abruf der Session-ID: {e}")

# ------------------------------------------------------------
# 3. Hilfsfunktionen für API-Aufrufe
# ------------------------------------------------------------
def api_get(endpoint, params=None, session_id=None, headers=None):
    """
    Führt einen GET-Request an die IBKR API aus.
    Verwendet das Session-Cookie sowie optionale zusätzliche Header.
    """
    url = f"{BASE_URL}{endpoint}"
    cookies = {"api": session_id} if session_id else None
    req_headers = headers or {}
    try:
        response = requests.get(
            url,
            params=params,
            cookies=cookies,
            headers=req_headers,
            verify=False,
            timeout=10
        )
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"  ⚠️ API-Fehler bei {endpoint}: {e}")
        return None

# ------------------------------------------------------------
# 4. Underlying-Vertrag suchen
# ------------------------------------------------------------
def find_underlying_contract(symbol, session_id):
    """
    Sucht den Basisvertrag (Underlying) für das angegebene Symbol.
    Gibt die conid und die Liste der verfügbaren Optionsmonate zurück.
    """
    print(f"⏳ Suche Basisvertrag für {symbol}...")
    endpoint = "/iserver/secdef/search"
    params = {"symbol": symbol}
    data = api_get(endpoint, params, session_id)

    if not data or not isinstance(data, list):
        raise Exception("Keine Daten von /iserver/secdef/search erhalten.")

    # Suche nach dem NASDAQ-Kontrakt
    for contract in data:
        if contract.get("description") == "NASDAQ":
            conid = contract.get("conid")
            if not conid:
                continue
            # Extrahiere Optionsmonate aus den Sections
            sections = contract.get("sections", [])
            for section in sections:
                if section.get("secType") == "OPT":
                    months_str = section.get("months", "")
                    if months_str:
                        months = months_str.split(";")
                        print(f"✅ Basisvertrag gefunden (conid: {conid})")
                        print(f"📅 Verfügbare Optionsmonate: {months[:5]}...")
                        return conid, months
    raise Exception(f"Kein passender NASDAQ-Kontrakt für {symbol} gefunden.")

# ------------------------------------------------------------
# 5. Nächste Verfallszyklen ermitteln
# ------------------------------------------------------------
def get_next_expiry_months(months_list, num_months=3):
    """
    Gibt die nächsten 'num_months' Verfallsmonate zurück,
    die nach dem aktuellen Datum liegen.
    """
    month_map = {
        "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
        "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12
    }
    today = datetime.now().date()
    valid_months = []

    for month_str in months_list:
        if len(month_str) < 5:
            continue
        month_code = month_str[:3]
        year_suffix = month_str[3:]
        try:
            year = 2000 + int(year_suffix) if int(year_suffix) >= 0 else 1900 + int(year_suffix)
            # Annahme: Verfall ist der 15. des Monats (für Vergleich ausreichend)
            expiry = datetime(year, month_map[month_code], 15).date()
            if expiry > today:
                valid_months.append(month_str)
        except (ValueError, KeyError):
            continue

    return valid_months[:num_months]

# ------------------------------------------------------------
# 6. Strikes für einen bestimmten Monat abrufen
# ------------------------------------------------------------
def get_strikes(conid, month, session_id):
    """
    Ruft die verfügbaren Strike-Preise für einen Optionsmonat ab.
    Gibt die Liste der Put-Strikes zurück.
    """
    endpoint = "/iserver/secdef/strikes"
    params = {
        "conid": conid,
        "sectype": "OPT",
        "month": month
    }
    data = api_get(endpoint, params, session_id)
    if not data:
        return []
    # Extrahiere Put-Strikes
    return data.get("put", [])

# ------------------------------------------------------------
# 7. Kontraktinformationen für einen bestimmten Strike abrufen
# ------------------------------------------------------------
def get_contract_info(conid, month, strike, right, session_id):
    """
    Ruft detaillierte Informationen zu einem Optionskontrakt ab.
    Gibt die Kontrakt-ID (conid) zurück.
    """
    endpoint = "/iserver/secdef/info"
    params = {
        "conid": conid,
        "sectype": "OPT",
        "month": month,
        "strike": strike,
        "right": right
    }
    data = api_get(endpoint, params, session_id)
    if not data or not isinstance(data, list) or len(data) == 0:
        return None
    contract = data[0]
    return {
        "conid": contract.get("conid"),
        "symbol": contract.get("symbol"),
        "strike": contract.get("strike"),
        "right": right,
        "maturity_date": contract.get("maturityDate"),
        "description": contract.get("description", "")
    }

# ------------------------------------------------------------
# 8. Marktdaten-Snapshot mit verzögerten Daten & Wiederholung
# ------------------------------------------------------------
def get_market_data_snapshot(conids, session_id):
    """
    Ruft Marktdaten (Delta, Open Interest, Last Price) für mehrere Kontrakte ab.
    Verwendet den Header 'DELAY: true' für 15-minütig verzögerte Daten.
    Führt einen zweiten Versuch nach 0,5 Sekunden aus, um Griechen zu berechnen.
    Gibt ein Dictionary {conid: {delta, open_interest, last_price}} zurück.
    """
    if not conids:
        return {}
    endpoint = "/iserver/marketdata/snapshot"
    fields = ["7314", "54", "31"]  # Delta, Open Interest, Last Price
    params = {
        "conids": ",".join(str(c) for c in conids),
        "fields": ",".join(fields)
    }
    # Header für verzögerte Daten aktivieren
    headers = {"DELAY": "true"}

    def fetch():
        """Interner Aufruf des Snapshots."""
        return api_get(endpoint, params, session_id, headers=headers)

    # Erster Versuch
    data = fetch()
    if not data or not isinstance(data, list):
        return {}

    # Kurze Pause, damit die API die Griechen berechnen kann
    time.sleep(0.5)

    # Zweiter Versuch (überschreibt die vorherigen Daten, da Griechen jetzt da sein sollten)
    data_retry = fetch()
    if data_retry and isinstance(data_retry, list):
        data = data_retry  # Verwende die Daten des zweiten Versuchs

    result = {}
    for item in data:
        conid = item.get("conid")
        if not conid:
            continue
        result[conid] = {
            "delta": item.get("7314"),
            "open_interest": item.get("54"),
            "last_price": item.get("31")
        }
    return result

# ------------------------------------------------------------
# 9. Logging-Funktionen
# ------------------------------------------------------------
def init_log_files():
    """Erstellt die beiden Logdateien mit Kopfzeilen."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    all_log = f"pltr_puts_all_log_{timestamp}.txt"
    filtered_log = f"pltr_puts_filtered_log_{timestamp}.txt"
    
    # All-Log
    with open(all_log, "w", encoding="utf-8") as f:
        f.write(f"=== Log-Datei: ALLE abgerufenen Put-Optionen (PLTR, verzögerte Daten) ===\n")
        f.write(f"Startzeit: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write("Format: Zeit | Strike | Verfall | Delta | Open Interest | Last Price\n\n")
    
    # Filtered-Log
    with open(filtered_log, "w", encoding="utf-8") as f:
        f.write(f"=== Log-Datei: Gefilterte Put-Optionen (Delta {DELTA_MIN} bis {DELTA_MAX}) ===\n")
        f.write(f"Startzeit: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write("Format: Zeit | Strike | Verfall | Delta | Open Interest | Last Price\n\n")
    
    return all_log, filtered_log

def write_log_entry(log_file, strike, maturity, delta, open_interest, last_price):
    """Schreibt einen Eintrag in die angegebene Logdatei."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if delta is not None:
        delta_str = f"{delta:.4f}"
    else:
        delta_str = "N/A"
    log_line = f"{timestamp} | Strike: {strike} | Verfall: {maturity} | Delta: {delta_str} | OI: {open_interest} | Last: {last_price}\n"
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(log_line)

# ------------------------------------------------------------
# 10. Hauptfunktion
# ------------------------------------------------------------
def main():
    print("=== IBKR Web API - Put-Optionen für PLTR (verzögerte Daten) ===\n")

    # Logdateien initialisieren
    all_log_file, filtered_log_file = init_log_files()
    print(f"📝 All-Logdatei: {all_log_file}")
    print(f"📝 Filtered-Logdatei: {filtered_log_file}")

    # Schritt 1: Session starten
    print("⏳ Initialisiere Session...")
    session_id = get_session_token()
    print(f"✅ Session-ID empfangen: {session_id[:10]}...")

    # Schritt 2: Underlying suchen
    underlying_conid, all_months = find_underlying_contract(SYMBOL, session_id)

    # Schritt 3: Nächste 3 Verfallszyklen auswählen
    expiry_months = get_next_expiry_months(all_months, 3)
    if not expiry_months:
        raise Exception("Keine zukünftigen Optionsmonate gefunden.")
    print(f"📅 Nächste 3 Verfallszyklen: {expiry_months}")

    # Schritt 4: Alle Put-Optionen sammeln
    print("\n⏳ Sammle Put-Optionen...")
    candidate_options = []  # Liste von Dictionaries mit Kontraktinformationen

    for month in expiry_months:
        print(f"  └─ Monat {month}...")
        strikes = get_strikes(underlying_conid, month, session_id)
        if not strikes:
            print(f"     ⚠️ Keine Strikes für {month} gefunden.")
            continue

        for strike in strikes:
            contract_info = get_contract_info(
                underlying_conid, month, strike, "P", session_id
            )
            if contract_info and contract_info.get("conid"):
                candidate_options.append(contract_info)
                if len(candidate_options) >= MAX_RESULTS * 3:
                    break

    if not candidate_options:
        raise Exception("Keine Put-Optionen gefunden.")

    print(f"\n✅ {len(candidate_options)} Put-Optionen als Kandidaten gefunden.\n")

    # Schritt 5: Marktdaten für alle Kandidaten abrufen (in Batches von 100)
    print("⏳ Lade Marktdaten (Delta, Open Interest, Last Price) mit verzögerter Datenquelle...")
    conids = [opt["conid"] for opt in candidate_options if opt.get("conid")]
    batch_size = 100
    all_market_data = {}

    for i in range(0, len(conids), batch_size):
        batch = conids[i:i+batch_size]
        print(f"  └─ Batch {i//batch_size + 1} ({len(batch)} conids)...")
        market_data = get_market_data_snapshot(batch, session_id)
        all_market_data.update(market_data)
        # Keine zusätzliche Pause nötig, da die interne Wiederholung bereits wartet

    # Schritt 6: Für jeden Kandidaten Logeintrag in ALL-Log schreiben,
    #            dann nach Delta filtern und ggf. in filtered_log + CSV übernehmen
    print("\n⏳ Verarbeite Marktdaten und filtere...")
    filtered_options = []

    for opt in candidate_options:
        conid = opt.get("conid")
        if not conid or conid not in all_market_data:
            continue

        delta_str = all_market_data[conid].get("delta")
        open_interest = all_market_data[conid].get("open_interest")
        last_price = all_market_data[conid].get("last_price")
        strike = opt.get("strike")
        maturity = opt.get("maturity_date")

        # Versuche Delta als float zu parsen
        delta = None
        if delta_str is not None:
            try:
                delta = float(delta_str)
            except (ValueError, TypeError):
                delta = None

        # IMMER in All-Log schreiben (auch wenn Delta None oder außerhalb Bereichs)
        write_log_entry(all_log_file, strike, maturity, delta, open_interest, last_price)

        # Prüfen ob Delta im gewünschten Bereich liegt
        if delta is not None and DELTA_MIN <= delta <= DELTA_MAX:
            opt["delta"] = delta
            opt["open_interest"] = open_interest
            opt["last_price"] = last_price
            filtered_options.append(opt)

            # In gefiltertes Log schreiben
            write_log_entry(filtered_log_file, strike, maturity, delta, open_interest, last_price)

            if len(filtered_options) >= MAX_RESULTS:
                break

    print(f"✅ {len(filtered_options)} Put-Optionen entsprechen dem Delta-Filter (von {len(candidate_options)} Kandidaten).\n")

    # Schritt 7: Ergebnisse als CSV speichern (nur gefilterte)
    if filtered_options:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"pltr_puts_delta_{DELTA_MIN}_{DELTA_MAX}_{timestamp}.csv"
        print(f"💾 Speichere gefilterte Ergebnisse in {filename}...")

        with open(filename, "w", newline="", encoding="utf-8") as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=CSV_HEADER)
            writer.writeheader()
            for opt in filtered_options:
                row = {key: opt.get(key) for key in CSV_HEADER}
                writer.writerow(row)
        print(f"✅ CSV-Datei gespeichert: {filename}")

        # Abschließende Metadaten in die Logdateien schreiben
        with open(filtered_log_file, "a", encoding="utf-8") as f:
            f.write(f"\n=== Ende des gefilterten Logs ===\n")
            f.write(f"Anzahl gefilterter Optionen: {len(filtered_options)}\n")
            f.write(f"CSV-Datei: {filename}\n")

        # Ausgabe der ersten 5 Ergebnisse auf der Konsole
        print("\n=== Erste 5 gefilterte Ergebnisse ===")
        for i, opt in enumerate(filtered_options[:5]):
            print(f"{i+1}. {opt['symbol']} | Strike: {opt['strike']} | "
                  f"Delta: {opt['delta']:.4f} | OI: {opt.get('open_interest')} | "
                  f"Last: {opt.get('last_price')}")
    else:
        print("⚠️ Keine Optionen mit dem gewünschten Delta-Bereich gefunden.")
        with open(filtered_log_file, "a", encoding="utf-8") as f:
            f.write("\n⚠️ Keine Optionen mit dem gewünschten Delta-Bereich gefunden.\n")

    with open(all_log_file, "a", encoding="utf-8") as f:
        f.write(f"\n=== Ende des All-Logs ===\n")
        f.write(f"Insgesamt abgerufene Kandidaten: {len(candidate_options)}\n")

    print(f"\n📝 All-Logdatei: {all_log_file}")
    print(f"📝 Gefilterte Logdatei: {filtered_log_file}")
    print("=== Skript abgeschlossen ===")

# ------------------------------------------------------------
# Ausführung
# ------------------------------------------------------------
if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\n❌ Fehler: {e}")
        # Versuche Fehler in die Logdateien zu schreiben (sofern bereits initialisiert)
        try:
            with open(all_log_file, "a", encoding="utf-8") as f:
                f.write(f"\n❌ FEHLER: {e}\n")
        except:
            pass
        try:
            with open(filtered_log_file, "a", encoding="utf-8") as f:
                f.write(f"\n❌ FEHLER: {e}\n")
        except:
            pass