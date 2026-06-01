"""
Korrigierte Version: Abruf der Optionskette von Palantir (PLTR) über die IBKR Web API.
Filter: Verfall innerhalb der nächsten 3 Zyklen, |Delta| <= 0.10.
Verbindungsparameter: Host 127.0.0.1, Port 4002.
Verwendet die korrekte 'live_marketdata_snapshot' Methode.
"""

import datetime
from typing import List, Dict, Any
from dateutil.relativedelta import relativedelta
from ibind import IbkrClient, ibind_logs_initialize

# ------------------------------------------------------------
# 1. Verbindung zum lokalen IBKR Gateway/Client Portal herstellen
# ------------------------------------------------------------
ibind_logs_initialize()

client = IbkrClient(host='127.0.0.1', port='4002')

# ------------------------------------------------------------
# 2. Hilfsfunktionen
# ------------------------------------------------------------
def get_expiry_dates(months: List[str]) -> List[str]:
    """
    Gibt die nächsten 3 Verfallszyklen zurück, die nach dem aktuellen Datum liegen.
    """
    today = datetime.date.today()
    valid_months = []
    for month in months:
        # Konvertiere MMMYY in ein Datum (Annahme: Verfall ist der dritte Freitag des Monats)
        try:
            month_map = {
                'JAN': 1, 'FEB': 2, 'MAR': 3, 'APR': 4, 'MAY': 5, 'JUN': 6,
                'JUL': 7, 'AUG': 8, 'SEP': 9, 'OCT': 10, 'NOV': 11, 'DEC': 12
            }
            month_code = month[:3]
            year_suffix = month[3:]
            year = 2000 + int(year_suffix) if int(year_suffix) < 50 else 1900 + int(year_suffix)
            expiry_date = datetime.date(year, month_map[month_code], 15)  # Ungefähres Datum
            if expiry_date > today:
                valid_months.append(month)
        except Exception as e:
            print(f"  ⚠️ Konnte Monat {month} nicht parsen: {e}")
    return valid_months[:3]  # Nächste 3 Zyklen

# ------------------------------------------------------------
# 3. Hauptfunktion zum Abruf der gefilterten Optionen
# ------------------------------------------------------------
def get_filtered_options(symbol: str, max_delta: float = 0.10) -> List[Dict[str, Any]]:
    """
    Ruft Optionen für ein gegebenes Symbol ab und filtert nach Verfallsdatum und Delta.
    Verwendet die nächsten 3 Verfallszyklen.
    """
    print(f"⏳ Suche Basisvertrag für {symbol}...")

    # Schritt 1: Basisvertrag (Underlying) suchen
    try:
        search_results = client.search_contract_by_symbol(symbol)
        if not search_results or not search_results.data:
            raise Exception(f"Keine Ergebnisse für Symbol {symbol} gefunden.")
        contracts = search_results.data
    except Exception as e:
        raise Exception(f"Fehler bei der Vertragssuche: {e}")

    underlying_conid = None
    opt_months_raw = []

    for contract in contracts:
        if "NASDAQ" in contract.get("description", "") or "SMART" in contract.get("description", ""):
            underlying_conid = contract.get("conid")
            for section in contract.get("sections", []):
                if section.get("secType") == "OPT":
                    months_str = section.get("months", "")
                    if months_str:
                        opt_months_raw = months_str.split(';')
                    break
            break

    if not underlying_conid or not opt_months_raw:
        raise Exception(f"Kein Basisvertrag oder keine Optionsmonate für {symbol} gefunden.")

    print(f"✅ Basisvertrag gefunden (conid: {underlying_conid})")

    # Schritt 2: Nächste 3 Verfallszyklen auswählen
    opt_months = get_expiry_dates(opt_months_raw)
    if not opt_months:
        raise Exception("Keine zukünftigen Optionsmonate gefunden.")

    print(f"📅 Filter: Verwendung der nächsten 3 Zyklen: {opt_months}")

    all_candidates = []  # Sammelt alle potenziellen Optionen (ohne Marktdaten)

    for month in opt_months:
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
                        info = client.search_secdef_info_by_conid(
                            conid=underlying_conid,
                            sec_type='OPT',
                            month=month,
                            strike=strike,
                            right=right
                        ).data

                        for contract in info:
                            all_candidates.append({
                                "conid": contract.get("conid"),
                                "symbol": contract.get("symbol"),
                                "strike": contract.get("strike"),
                                "right": right,
                                "maturity_date": contract.get("maturityDate"),
                                "description": contract.get("description", "")
                            })
                    except Exception as e:
                        print(f"      ⚠️ Fehler bei Detailabfrage {right} {strike}: {e}")
        except Exception as e:
            print(f"   ⚠️ Fehler beim Abruf der Strikes für {month}: {e}")

    if not all_candidates:
        raise Exception("Keine Optionen für die ausgewählten Zyklen gefunden.")

    print(f"⏳ Lade Marktdaten (Delta, Open Interest) für {len(all_candidates)} Optionen...")

    # Schritt 5: Marktdaten (Snapshot) für alle gesammelten Optionen abrufen
    final_options = []
    batch_size = 90

    for i in range(0, len(all_candidates), batch_size):
        batch = all_candidates[i:i+batch_size]
        conids = [opt['conid'] for opt in batch]

        try:
            # Verwendung der korrekten 'live_marketdata_snapshot' Methode
            snapshot_result = client.live_marketdata_snapshot(conids=conids, fields=["31", "54", "7314"])
            if not snapshot_result or not snapshot_result.data:
                print(f"   ⚠️ Keine Snapshot-Daten für Batch {i//batch_size + 1} erhalten.")
                continue
            snapshot = snapshot_result.data
        except Exception as e:
            print(f"   ⚠️ Fehler beim Snapshot-Abruf für Batch {i//batch_size + 1}: {type(e).__name__}: {e}")
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
                    if abs(delta) <= max_delta:
                        option['delta'] = delta
                        option['open_interest'] = data.get('54')
                        option['last_price'] = data.get('31')
                        final_options.append(option)
                except (ValueError, TypeError):
                    continue

    print(f"✅ Gefilterte Optionen: {len(final_options)} (von {len(all_candidates)} Kandidaten)")
    return final_options

# ------------------------------------------------------------
# 4. Ausführung
# ------------------------------------------------------------
if __name__ == "__main__":
    try:
        options = get_filtered_options("PLTR", max_delta=0.10)

        print("\n=== Ergebnis (erste 5 Optionen) ===")
        for opt in options[:5]:
            print(f"{opt['symbol']} | Strike: {opt['strike']:6.2f} | "
                  f"Typ: {'Call' if opt['right'] == 'C' else 'Put'} | "
                  f"Verfall: {opt['maturity_date']} | Delta: {opt.get('delta'):.4f} | "
                  f"OI: {opt.get('open_interest')} | Letzter Preis: {opt.get('last_price')}")

        if not options:
            print("\nℹ️ Keine Optionen gefunden, die den Kriterien entsprechen.")

    except Exception as e:
        print(f"❌ Fehler im Hauptprogramm: {e}")