#!/usr/bin/env python3
# test_delayed_snapshot.py

import requests
import json

# Konfiguration
BASE_URL = "http://localhost:4002"
HEADERS = {"User-Agent": "Mozilla/5.0"}

def test_delayed_marketdata():
    # 1. Zuerst die Conid für AAPL suchen
    search_url = f"{BASE_URL}/v1/api/iserver/secdef/search?symbol=AAPL"
    print(f"🔍 Suche AAPL Conid: {search_url}")
    try:
        resp = requests.get(search_url, headers=HEADERS, verify=False, timeout=10)
        resp.raise_for_status()
        contracts = resp.json()
        conid = None
        for c in contracts:
            if c["description"] == "NASDAQ":
                conid = c["conid"]
                break
        if not conid:
            print("❌ Keine Conid für AAPL/NASDAQ gefunden.")
            return
        print(f"✅ Gefundene Conid: {conid}")
    except Exception as e:
        print(f"❌ Fehler bei der Suche: {e}")
        return

    # 2. Marktdaten mit verzögertem Snapshot abrufen
    snapshot_url = f"{BASE_URL}/v1/api/iserver/marketdata/snapshot?conids={conid}&delay=1&snapshot=1"
    print(f"\n📡 Rufe verzögerten Snapshot ab: {snapshot_url}")
    try:
        resp = requests.get(snapshot_url, headers=HEADERS, verify=False, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        print("\n📊 Antwort (JSON):")
        print(json.dumps(data, indent=2))
        
        # Extrahiere wichtige Felder (falls vorhanden)
        if data and len(data) > 0:
            item = data[0]
            print("\n📈 Ausgewählte Felder:")
            print(f"  - Letzter Preis (31): {item.get('31', 'nicht verfügbar')}")
            print(f"  - Bid (84): {item.get('84', 'nicht verfügbar')}")
            print(f"  - Ask (85): {item.get('85', 'nicht verfügbar')}")
            print(f"  - Delta (86): {item.get('86', 'nicht verfügbar')}")
            print(f"  - Gamma (87): {item.get('87', 'nicht verfügbar')}")
            print(f"  - Theta (88): {item.get('88', 'nicht verfügbar')}")
            print(f"  - Vega (89): {item.get('89', 'nicht verfügbar')}")
        else:
            print("⚠️ Keine Daten erhalten.")
    except Exception as e:
        print(f"❌ Fehler beim Snapshot: {e}")

if __name__ == "__main__":
    # Zertifikatswarnungen ignorieren (bei HTTP nicht nötig, aber sicherheitshalber)
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    
    test_delayed_marketdata()