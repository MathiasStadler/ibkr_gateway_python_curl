
import os
import datetime
from dateutil.relativedelta import relativedelta
from ibind import IbkrClient
from ibind.support.base_models import SecDefSearchParams, SecDefStrikesParams, SecDefInfoParams

# Initialisierung des IBind-Clients
# Stellen Sie sicher, dass Ihre OAuth-Zugangsdaten als Umgebungsvariablen gesetzt sind
client = IbkrClient()

def get_options_chain(symbol: str, exchange: str = "NASDAQ", days_to_expiry: int = 30, max_delta: float = 0.10):
    """
    Ruft die Optionskette für ein gegebenes Symbol ab, filtert nach Verfallsdatum und Delta.
    """
    print(f"⏳ Suche nach dem Underlying-Kontrakt für {symbol}...")
    # 1. Vertrag suchen (secdef/search) 
    # Filtern nach der gewünschten Börse, um den richtigen Kontrakt zu finden
    search_results = client.iserver_secdef_search({'symbol': symbol})

    under_conid = None
    available_months = [] # Enthält Strings wie 'JAN25', 'FEB25', ...

    # Durchsuche die Ergebnisse nach dem gewünschten Underlying
    for contract in search_results:
        if contract.get("description") == exchange:
            under_conid = contract["conid"]
            for section in contract.get("sections", []):
                if section.get("secType") == "OPT":
                    # Die verfügbaren Monate sind als Semikolon-getrennte Zeichenkette
                    available_months = section.get("months", "").split(';')
                    break
            break

    if not under_conid or not available_months:
        raise Exception(f"Konnte das Underlying {symbol} oder Optionsmonate nicht finden.")

    print(f"✅ Underlying gefunden (conid: {under_conid})")

    # 2. Berechnung des Cutoff-Datums für die nächsten X Tage
    cutoff_date = datetime.date.today() + relativedelta(days=days_to_expiry)
    print(f"📅 Filter: Verfallsdatum vor dem {cutoff_date}")

    # 3. Verfügbare Ablaufmonate und ihre genauen Daten ermitteln
    # Wir müssen für jeden potenziell relevanten Monat die Strikes abrufen
    all_options = []
    expirations_to_check = []

    # Konvertiere MMMYY (z.B. 'JAN25') in ein datetime-Objekt für den Vergleich
    for month_str in available_months:
        # Annahme: Der letzte Handelstag ist der dritte Freitag des Monats.
        # Für eine vereinfachte Filterung, die auf dem API-Antworttext basiert,
        # gleichen wir den Monat ab. Eine genauere Methode wird später gezeigt.
        expirations_to_check.append(month_str)

    # 4. Für jeden relevanten Monat die Strikes und Kontraktinformationen abrufen
    print("⏳ Lade Optionen...")
    all_strikes = []
    for month in expirations_to_check:
        # Strikes für diesen Monat abrufen
        strikes_params = SecDefStrikesParams(conid=under_conid, secType="OPT", month=month)
        strikes_response = client.iserver_secdef_strikes(strikes_params)
        
        # Kombiniere Calls und Puts für diesen Monat
        if 'call' in strikes_response:
            all_strikes.extend(strikes_response['call'])
        if 'put' in strikes_response:
            all_strikes.extend(strikes_response['put'])
        
        # Für jeden gefundenen Strike die detaillierten Kontraktinformationen abrufen
        for strike in set(all_strikes):
            for contract_type in ['C', 'P']:
                info_params = SecDefInfoParams(conid=under_conid, secType="OPT", month=month, strike=strike, right=contract_type)
                try:
                    info_response = client.iserver_secdef_info(info_params)
                    # Die Antwort ist eine Liste, die das gewünschte Objekt enthält.
                    for contract in info_response:
                        # Verfallsdatum aus der API-Antwort parsen (Format: 'YYYYMMDD')
                        expiry_date_str = contract.get('maturityDate')
                        if expiry_date_str:
                            expiry_date = datetime.datetime.strptime(expiry_date_str, '%Y%m%d').date()
                            # Filter nach Verfallsdatum
                            if expiry_date <= cutoff_date:
                                contract_info = {
                                    "conid": contract.get("conid"),
                                    "symbol": contract.get("symbol"),
                                    "strike": contract.get("strike"),
                                    "right": contract_type,
                                    "maturity_date": expiry_date_str,
                                }
                                all_options.append(contract_info)
                except Exception as e:
                    print(f"⚠️ Fehler beim Abruf von {contract_type} {strike}: {e}")

    if not all_options:
        raise Exception("Keine Optionen mit dem angegebenen Verfallsdatum gefunden.")

    # 5. Marktdaten (inkl. Delta und Open Interest) für alle gefundenen Kontrakte abrufen
    # Die API erlaubt maximal 100 conids pro Anfrage
    print("⏳ Lade Marktdaten für die gefundenen Optionen...")
    final_options = []
    
    # Teile die Liste in Batches von 100 auf, um das Limit der API einzuhalten
    batch_size = 90 # Konservativer Wert für Stabilität
    for i in range(0, len(all_options), batch_size):
        batch = all_options[i:i+batch_size]
        conids = [opt['conid'] for opt in batch]
        
        # Felder: 31 = Last Price, 54 = Open Interest, 7310 = Theta (Beispiel), 7314 = Delta
        # Eine vollständige Liste der Feld-IDs finden Sie in der IBKR API-Dokumentation.
        snapshot_data = client.iserver_marketdata_snapshot_get(conids=conids, fields=["31", "54", "7314"])
        
        # Verarbeite die Snapshot-Daten
        for data in snapshot_data:
            conid = data.get('conid')
            if conid is None:
                continue
            # Finde das entsprechende Options-Dictionary
            matching_option = next((opt for opt in all_options if opt['conid'] == conid), None)
            if matching_option:
                # Delta parsen (Feld "7314" ist ein String)
                delta = None
                delta_str = data.get('7314')
                if delta_str:
                    try:
                        delta = float(delta_str)
                    except ValueError:
                        pass
                
                # Open Interest parsen (Feld "54")
                open_interest = None
                oi_str = data.get('54')
                if oi_str:
                    try:
                        open_interest = int(oi_str)
                    except ValueError:
                        pass

                # Füge die Marktdaten hinzu
                matching_option['delta'] = delta
                matching_option['open_interest'] = open_interest
                matching_option['last_price'] = data.get('31') # Letzter Handelspreis
                
                # Filter nach Delta, falls Delta verfügbar ist
                if delta is not None:
                    # Wir prüfen den absoluten Wert für Puts (Delta negativ) und Calls (Delta positiv)
                    if abs(delta) <= max_delta:source .venv/bin/activate
                        final_options.append(matching_option)
                else:
                    # Optionen ohne Delta werden standardmäßig ignoriert
                    pass

    print(f"✅ Erfolg! {len(final_options)} Optionen nach Filtern gefunden.")
    return final_options

if __name__ == "__main__":
    try:
        # Rufen Sie die Optionskette für Palantir (PLTR) ab
        # Filter: Verfall innerhalb der nächsten 30 Tage, |Delta| <= 0.10
        options_data = get_options_chain("PLTR", days_to_expiry=30, max_delta=0.10)

        print("\n--- Beispiel der ersten 5 Optionen ---")
        for contract in options_data[:5]:
            # Ausgabe der wichtigsten Informationen
            print(f"Symbol: {contract['symbol']}, Strike: {contract['strike']}, "
                  f"Type: {'Call' if contract['right'] == 'C' else 'Put'}, "
                  f"Expiry: {contract['maturity_date']}, Delta: {contract.get('delta')}, "
                  f"Open Interest: {contract.get('open_interest')}")
        
    except Exception as e:
        print(f"❌ Ein Fehler ist aufgetreten: {e}")
