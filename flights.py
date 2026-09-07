#!/usr/bin/env python3
"""Flugpreise München (MUC) → Da Nang (DAD) für die nächste Woche."""

import requests
import time
from datetime import datetime, timedelta
import re

# Ziel: München (MUC) → Da Nang (DAD)
# Zeitraum: nächste 7 Tage
from_date = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
to_date = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")

# Google Flights API (kostenlos, keine Anmeldung nötig)
url = (
    "https://www.google.com/travel/flights"
    "?adults=1&children=0&"
    "originPlaceName=M%C3%BCnchen&destinationsPlaceName=Da%20Nang"
    "&outboundDate=" + from_date + "&inboundDate=" + to_date
)

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}

try:
    response = requests.get(url, headers=headers, timeout=30)
    response.raise_for_status()
except Exception as e:
    print(f"❌ Fehler beim Abrufen der Flüge: {e}")
    exit(1)

html = response.text
print("🔍 Flüge München → Da Nang (nächste Woche)\n")

# Google Flights gibt HTML zurück – wir parsen die Tabellen
# Suche nach Preisanzeigen im HTML
matches = re.findall(r"€[0-9,]+", html)
if not matches:
    print("⚠️ Keine Preise gefunden – Google Flights könnte keine Ergebnisse haben.")
    exit(1)

# Sortiere nach niedrigstem Preis
matches.sort(key=lambda x: float(re.search(r"[0-9,]+", x).group() or ""))

print(f"{'Datum':<12} {'Preis (EUR)':<15} {'Flug'}\n")
for i, price_str in enumerate(matches[:10], 1):
    price_match = re.search(r"€([0-9,]+)", price_str)
    if price_match:
        price = float(price_match.group(1).replace(",", ""))
        print(f"{i:<12} {price:<15} {price_str}")
    else:
        print(f"{i:<12} {'':<15} {price_str}")

print(f"\n✅ Gesucht: München (MUC) → Da Nang (DAD) für {from_date} bis {to_date}")
print("💡 Die Preise sind ungefähr – für exakte Buchung empfehle ich Google Flights oder Skyscanner.")