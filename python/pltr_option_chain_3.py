"""
Abruf der Optionskette von Palantir (PLTR) über die IBKR Web API.
Filter: Verfall innerhalb der nächsten 30 Tage, |Delta| <= 0.10.
Verbindungsparameter: Host 127.0.0.1, Port 4002.
"""

import datetime
from dateutil.relativedelta import relativedelta
from ibind import IbkrClient, ibind_logs_initialize
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ------------------------------------------------------------
# 1. Verbindung zum lokalen IBKR Gateway/Client Portal herstellen
# ------------------------------------------------------------
ibind_logs_initialize()  # Optionale Log-Initialisierung

client = IbkrClient(
    host='127.0.0.1',
    port='4002',
    timeout=10,
    max_retries=3,
    use_session=True
)

# ------------------------------------------------------------
# 2. Hauptfunktion zum Abruf der gefilterten Optionen
# ------------------------------------------------------------
def get_filtered_options(symbol: str, days_to_expiry: int = 30, max_delta: float = 0.10):
    """
    Ruft Optionen für ein gegebenes Symbol ab und filtert nach Verfallsdatum und Delta.

    Args:
        symbol (str): Aktiensymbol (z. B. 'PLTR')
        days_to_expiry (int): Max. Tage bis zum Verfall (Standard 30)
        max_delta (float): Maximaler absoluter Delta-Wert (Standard 0.10)

    Returns:
        List[dict]: Gefilterte Optionskontrakte mit Marktdaten.
    """
    print(f"⏳ Suche Basisvertrag für {symbol}...")

    # Schritt 1: Basisvertrag (Underlying) suchen
    contracts = client.search_contract_by_symbol(symbol).data

    underlying_conid = None
    opt_months = []

    for contract in contracts:
        # Prüfen, ob der Vertrag an der NASDAQ notiert (Anpassung ggf. nötig)
        if "NASDAQ" in contract.get("description", ""):
            underlying_conid = contract.get("conid")
            # Optionsmonate auslesen
            for section in contract.get("sections", []):
                if section.get("secType") == "OPT":
                    months_str = section.get("months", "")
                    if months_str:
                        opt_months = months_str.split(';')
                    break
            break

    if not underlying_conid or not opt_months:
        raise Exception(f"Kein passender Basisvertrag oder keine Optionsmonate für {symbol} gefunden. API-Antwort: {contracts}")

    print(f"✅ Basisvertrag gefunden (conid: {underlying_conid})")

    # Schritt 2: Datumsfilter – nur Optionen mit Verfall innerhalb der nächsten X Tage
    cutoff_date = datetime.date.today() + relativedelta(days=days_to_expiry)
    print(f"📅 Filter: Verfallsdatum <= {cutoff_date}")

    all_candidates = []  # Sammelt alle potenziellen Optionen (ohne Marktdaten)

    # Begrenzung auf die ersten 3 Optionsmonate, um API-Last zu reduzieren
    for month in opt_months[:3]:
        print(f"   └─ Verarbeite Monat: {month}")
        try:
            # Schritt 3: Strike-Preise für diesen Monat abrufen
            strikes_data = client.search_strikes_by_conid(
                conid=underlying_conid,
                sec_type='OPT',
                month=month
            ).data

            # Schritt 4: Für Calls (C) und Puts (P) die Details abrufen
            for right in ['C', 'P']:
                strikes_list = strikes_data.get('call' if right == 'C' else 'put', [])
                for strike in strikes_list:
                    try:
                        # Details zu diesem spezifischen Optionskontrakt
                        info = client.search_secdef_info_by_conid(
                            conid=underlying_conid,
                            sec_type='OPT',
                            month=month,
                            strike=strike,
                            right=right
                        ).data

                        for contract in info:
                            maturity_str = contract.get('maturityDate')
                            if maturity_str:
                                expiry_date = datetime.datetime.strptime(maturity_str, '%Y%m%d').date()
                                # Filter nach Verfallsdatum
                                if expiry_date <= cutoff_date:
                                    all_candidates.append({
                                        "conid": contract.get("conid"),
                                        "symbol": contract.get("symbol"),
                                        "strike": contract.get("strike"),
                                        "right": right,
                                        "maturity_date": maturity_str,
                                        "description": contract.get("description", "")
                                    })
                    except Exception as e:
                        print(f"⚠️ Fehler bei Detailabfrage {symbol} {month} {right} {strike}: {e}")
        except Exception as e:
            print(f"⚠️ Fehler beim Abruf der Strikes für {month}: {e}")

    if not all_candidates:
        raise Exception(f"Keine Optionen mit Verfall <= {cutoff_date} gefunden.")

    print(f"⏳ Lade Marktdaten (Delta, Open Interest) für {len(all_candidates)} Optionen...")

    # Schritt 5: Marktdaten (Snapshot) für alle gesammelten Optionen abrufen
    final_options = []
    batch_size = 90  # API erlaubt max. 100 conids pro Snapshot

    for i in range(0, len(all_candidates), batch_size):
        batch = all_candidates[i:i+batch_size]
        conids = [opt['conid'] for opt in batch]

        try:
            # Felder: 31 = Last Price, 54 = Open Interest, 7314 = Delta
            snapshot = client.marketdata_snapshot_get(conids=conids, fields=["31", "54", "7314"]).data
        except Exception as e:
            print(f"⚠️ Fehler beim Snapshot-Abruf für Batch {i//batch_size + 1}: {e}")
            continue

        for data in snapshot:
            conid = data.get('conid')
            option = next((opt for opt in batch if opt['conid'] == conid), None)
            if not option:
                continue

            delta_str = data.get('7314')
            if delta_str is not None:
                try:
                    delta = float(delta_str)
                    # Filter nach absolutem Delta (Puts haben negatives Delta)
                    if abs(delta) <= max_delta:
                        option['delta'] = delta
                        option['open_interest'] = data.get('54')   # Open Interest
                        option['last_price'] = data.get('31')     # Letzter Preis
                        final_options.append(option)
                except ValueError:
                    # Delta konnte nicht in Float umgewandelt werden
                    continue
            # Optionen ohne Delta werden ignoriert (da Filter nicht anwendbar)

    print(f"✅ Gefilterte Optionen: {len(final_options)} (von {len(all_candidates)} Kandidaten)")
    return final_options

# ------------------------------------------------------------
# 3. Ausführung
# ------------------------------------------------------------
if __name__ == "__main__":
    try:
        options = get_filtered_options("PLTR", days_to_expiry=30, max_delta=0.10)

        print("\n=== Ergebnis (erste 5 Optionen) ===")
        for opt in options[:5]:
            print(f"{opt['symbol']} | Strike: {opt['strike']:6.2f} | "
                  f"Typ: {'Call' if opt['right'] == 'C' else 'Put'} | "
                  f"Verfall: {opt['maturity_date']} | Delta: {opt.get('delta'):.4f} | "
                  f"OI: {opt.get('open_interest')} | Letzter Preis: {opt.get('last_price')}")

        # Speichern der gesamten Liste als JSON (optional)
        # import json
        # with open("pltr_options.json", "w") as f:
        #     json.dump(options, f, indent=2)

    except Exception as e:
        print(f"❌ Fehler im Hauptprogramm: {e}")