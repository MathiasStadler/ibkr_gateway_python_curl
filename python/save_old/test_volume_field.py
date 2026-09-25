#!/usr/bin/env python3
"""Finde die korrekte IBKR Feld-ID für Volume bei Delayed-Daten."""
import urllib3
import requests
import json

urllib3.disable_warnings()

conids = "836941335,833016763"

# Teste verschiedene Feld-IDs die möglicherweise Volume liefern
candidates = ["27", "28", "29", "30", "84", "85", "86", "87", "88", "89"]

for fid in candidates:
    url = f"https://localhost:4002/v1/api/iserver/marketdata/snapshot?conids={conids}&fields={fid}&snapshot=0"
    try:
        resp = requests.get(url, verify=False, timeout=10)
        data = resp.json()
        print(f"Feld {fid}: {json.dumps(data, indent=2)}")
    except Exception as e:
        print(f"Feld {fid}: ERROR {e}")
    print("-" * 40)
