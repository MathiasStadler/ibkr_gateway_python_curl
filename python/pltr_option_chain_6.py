"""
Abruf von Put-Optionen für Palantir (PLTR) über die IBKR Web API (ibind).
Filter: Delta zwischen -0,50 und -0,25, max. 30 Kontrakte.
Verbindung: 127.0.0.1:4002
"""

import datetime
from typing import List, Dict, Any
from dateutil.relativedelta import relativedelta
from ibind import IbkrClient, ibind_logs_initialize

# ------------------------------------------------------------
# 1. Verbindung zum lokalen IBKR Gateway
# ------------------------------------------------------------
ibind_logs_initialize()
client = IbkrClient(host='127.0.0.1', port='4002')

# Der ibind-Client verwendet den Snapshot-Endpunkt 'iserver/marketdata/snapshot'.
# Ohne Echtzeit-Abonnements liefert IBKR automatisch verzögerte Daten (ca. 15-20 Min.).
# Ein manueller Aufruf einer 'set_market_data_type'-Methode ist im ibind nicht vorgesehen.
# ------------------------------------------------------------

def get_next_expiry_months(months: List[str]) -> List[str]:
    """Gibt die nächsten 3 Verfallsmonate zurück (basierend auf dem aktuellen Datum)."""
    today = datetime.date.today()
    month_map = {
        'JAN': 1, 'FEB': 2, 'MAR': 3, 'APR': 4, 'MAY': 5, 'JUN': 6,
        'JUL': 7, 'AUG': 8, 'SEP': 9, 'OCT': 10, 'NOV': 11, 'DEC': 12
    }
    valid = []
    for m in months:
        try:
            code = m[:3]
            year_suffix = m[3:]
            year = 2000 + int(year_suffix) if int(year_suffix) < 50 else 1900 + int(year_suffix)
            expiry = datetime.date(year, month_map[code], 15)
            if expiry > today:
                valid.append(m)
        except Exception:
            continue
    return valid[:3]

def get_filtered_puts(symbol: str, max_results: int = 30) -> List[Dict[str, Any]]:
    """
    Ruft Put-Optionen ab und filtert nach Delta zwischen -0,50 und -0,25.
    """
    print(f"⏳ Suche Basisvertrag für {symbol}...")

    search_results = client.search_contract_by_symbol(symbol)
    if not search_results or not search_results.data:
        raise Exception(f"Kein Kontrakt für {symbol} gefunden.")
    
    contracts = search_results.data
    underlying_conid = None
    raw_months = []

    for c in contracts:
        if "NASDAQ" in c.get("description", "") or "SMART" in c.get("description", ""):
            underlying_conid = c.get("conid")
            for section in c.get("sections", []):
                if section.get("secType") == "OPT":
                    months_str = section.get("months", "")
                    if months_str:
                        raw_months = months_str.split(';')
                    break
            break

    if not underlying_conid or not raw_months:
        raise Exception("Keine Optionsmonate gefunden.")

    print(f"✅ Underlying conid: {underlying_conid}")

    expiries = get_next_expiry_months(raw_months)
    print(f"📅 Nächste Verfallzyklen: {expiries}")

    put_candidates = []
    for month in expiries:
        print(f"   └─ Monat {month}...")
        try:
            strikes_data = client.search_strikes_by_conid(
                conid=underlying_conid,
                sec_type='OPT',
                month=month
            ).data
            puts = strikes_data.get('put', [])
            for strike in puts:
                try:
                    info = client.search_secdef_info_by_conid(
                        conid=underlying_conid,
                        sec_type='OPT',
                        month=month,
                        strike=strike,
                        right='P'
                    ).data
                    for contract in info:
                        put_candidates.append({
                            "conid": contract.get("conid"),
                            "symbol": contract.get("symbol"),
                            "strike": contract.get("strike"),
                            "right": "P",
                            "maturity_date": contract.get("maturityDate"),
                            "description": contract.get("description", "")
                        })
                except Exception as e:
                    print(f"      ⚠️ Fehler bei Strike {strike}: {e}")
        except Exception as e:
            print(f"   ⚠️ Fehler bei Strikes für {month}: {e}")

    if not put_candidates:
        raise Exception("Keine Put-Optionen gefunden.")

    print(f"⏳ Lade Marktdaten für {len(put_candidates)} Puts...")

    final_puts = []
    batch_size = 90
    for i in range(0, len(put_candidates), batch_size):
        batch = put_candidates[i:i+batch_size]
        conids = [opt['conid'] for opt in batch]
        try:
            # Snapshot-Daten: Felder 31 = Last Price, 54 = Open Interest, 7314 = Delta
            snap = client.live_marketdata_snapshot(conids=conids, fields=["31", "54", "7314"])
            if not snap or not snap.data:
                continue
            for data in snap.data:
                conid = data.get('conid')
                opt = next((o for o in batch if o['conid'] == conid), None)
                if not opt:
                    continue
                delta_str = data.get('7314')
                if delta_str:
                    try:
                        delta = float(delta_str)
                        # Filter: Delta zwischen -0,50 und -0,25
                        if -0.50 <= delta <= -0.25:
                            opt['delta'] = delta
                            opt['open_interest'] = data.get('54')
                            opt['last_price'] = data.get('31')
                            final_puts.append(opt)
                            if len(final_puts) >= max_results:
                                break
                    except (ValueError, TypeError):
                        continue
        except Exception as e:
            print(f"   ⚠️ Snapshot-Fehler Batch {i//batch_size + 1}: {e}")
        if len(final_puts) >= max_results:
            break

    final_puts = final_puts[:max_results]
    print(f"✅ {len(final_puts)} Puts mit Delta im Bereich [-0,50, -0,25] gefunden.")
    return final_puts

if __name__ == "__main__":
    try:
        options = get_filtered_puts("PLTR", max_results=30)
        if not options:
            print("Keine passenden Put-Optionen gefunden.")
        else:
            print("\n=== Gefundene Put-Optionen (max. 30) ===")
            for opt in options:
                print(f"{opt['symbol']} | Strike: {opt['strike']:6.2f} | "
                      f"Verfall: {opt['maturity_date']} | Delta: {opt.get('delta'):.4f} | "
                      f"OI: {opt.get('open_interest')} | Letzter Preis: {opt.get('last_price')}")
    except Exception as e:
        print(f"❌ Fehler: {e}")