import os
import datetime
from dateutil.relativedelta import relativedelta
from ibind import IbkrClient, ibind_logs_initialize

# Initialisierung
ibind_logs_initialize()
client = IbkrClient()

def get_filtered_options(symbol: str, days_to_expiry: int = 30, max_delta: float = 0.10):
    """
    Ruft gefilterte Optionsdaten ab.
    
    Args:
        symbol (str): Aktiensymbol (z.B. 'PLTR')
        days_to_expiry (int): Anzahl der Tage bis zum Verfall (Standard: 30)
        max_delta (float): Maximaler Delta-Wert für Calls/Puts (Standard: 0.10)
    """
    print(f"⏳ Suche Vertrag für {symbol}...")
    # 1. Basisvertrag suchen
    contracts = client.search_contract_by_symbol(symbol).data
    
    underlying_contract = None
    opt_months = []
    for contract in contracts:
        # NASDAQ als Beispiel
        if "NASDAQ" in contract.get("description", ""):
            underlying_contract = contract
            for section in contract.get("sections", []):
                if section.get("secType") == "OPT":
                    opt_months = section.get("months", "").split(';')
                    break
            break

    if not underlying_contract or not opt_months:
        raise Exception(f"Kein Underlying oder keine Optionsmonate für {symbol} gefunden. API-Antwort: {contracts}")

    print(f"✅ Basisvertrag gefunden (conid: {underlying_contract['conid']})")
    cutoff_date = datetime.date.today() + relativedelta(days=days_to_expiry)
    print(f"📅 Filter: Verfallsdatum vor {cutoff_date}")

    all_options = []
    # 2. Iteriere durch die ersten 3 Monate (zur Begrenzung der API-Last)
    for month in opt_months[:3]:
        try:
            # 3. Strikes für den aktuellen Monat abrufen
            strikes_data = client.search_strikes_by_conid(
                conid=underlying_contract['conid'],
                sec_type='OPT',
                month=month
            ).data
            
            # 4. Für jeden Strike und jeden Typ (Call/Put) Details abrufen
            for right in ['C', 'P']:
                strikes_list = strikes_data.get('call' if right == 'C' else 'put', [])
                for strike in strikes_list:
                    try:
                        info = client.search_secdef_info_by_conid(
                            conid=underlying_contract['conid'],
                            sec_type='OPT',
                            month=month,
                            strike=strike,
                            right=right
                        ).data
                        
                        for contract in info:
                            maturity = contract.get('maturityDate')
                            if maturity:
                                expiry_date = datetime.datetime.strptime(maturity, '%Y%m%d').date()
                                # 5. Filter: Verfallsdatum <= 30 Tage
                                if expiry_date <= cutoff_date:
                                    all_options.append({
                                        "conid": contract.get("conid"),
                                        "symbol": contract.get("symbol"),
                                        "strike": contract.get("strike"),
                                        "right": right,
                                        "maturity_date": maturity,
                                        "description": contract.get("description", "")
                                    })
                    except Exception as e:
                        print(f"⚠️ Keine Details für {symbol} {month} {right} {strike}: {e}")
        except Exception as e:
            print(f"⚠️ Fehler beim Abruf der Strikes für {month}: {e}")

    if not all_options:
        raise Exception(f"Keine Optionen mit Verfall <= {cutoff_date} gefunden.")

    # 6. Marktdaten (Delta & Open Interest) für alle gesammelten Kontrakte abrufen
    print(f"⏳ Lade Daten für {len(all_options)} Optionen (Delta, Open Interest)...")
    final_options = []
    batch_size = 90
    for i in range(0, len(all_options), batch_size):
        batch = all_options[i:i+batch_size]
        conids = [opt['conid'] for opt in batch]
        try:
            # Felder: 31 = Letzter Preis, 54 = Open Interest, 7314 = Delta
            snapshot = client.marketdata_snapshot_get(conids=conids, fields=["31", "54", "7314"]).data
        except Exception as e:
            print(f"⚠️ Fehler beim Abruf der Marktdaten: {e}")
            continue

        for data in snapshot:
            conid = data.get('conid')
            option = next((opt for opt in batch if opt['conid'] == conid), None)
            if not option:
                continue

            delta = data.get('7314')
            if delta is not None:
                try:
                    delta = float(delta)
                    # 7. Filter nach absolutem Delta
                    if abs(delta) <= max_delta:
                        option['delta'] = delta
                        option['open_interest'] = data.get('54')
                        option['last_price'] = data.get('31')
                        final_options.append(option)
                except ValueError:
                    continue
    return final_options

if __name__ == "__main__":
    try:
        options = get_filtered_options("PLTR", days_to_expiry=30, max_delta=0.10)
        print(f"\n✅ {len(options)} gefilterte Optionen gefunden.\n")
        for contract in options[:5]:
            print(f"{contract['symbol']} - Strike: {contract['strike']} | "
                  f"Typ: {'Call' if contract['right'] == 'C' else 'Put'} | "
                  f"Verfall: {contract['maturity_date']} | Delta: {contract.get('delta')} | "
                  f"OI: {contract.get('open_interest')}")
    except Exception as e:
        print(f"❌ Fehler: {e}")