#!/usr/bin/env python3
"""Test script to find correct IBKR field IDs for volume and open interest."""
import sys
import urllib3
import requests
from datetime import datetime

urllib3.disable_warnings()

# Test conids from the TREX example
conids = "836941335,833016763"

# Try various field IDs that might be volume/open interest
candidate_fields = {
    "27": "volume (IBKR doc)",
    "84": "open_interest (IBKR doc)",
    "86": "delta",
    "88": "theta",
    "29": "unknown-29",
    "30": "unknown-30",
    "86": "delta",
    "87": "gamma",
    "89": "vega",
    "107": "rho",
}

# Build field string
field_ids = ",".join(candidate_fields.keys())

url = f"https://localhost:4002/v1/api/iserver/marketdata/snapshot?conids={conids}&fields={field_ids}&snapshot=0"

try:
    resp = requests.get(url, verify=False, timeout=10)
    print(f"Status: {resp.status_code}")
    data = resp.json()
    print(f"Response: {data}")
except Exception as e:
    print(f"Error: {e}")
    sys.exit(1)
