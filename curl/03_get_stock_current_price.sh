#!/bin/bash

# Prüfen, ob jq installiert ist
if ! command -v jq &> /dev/null; then
    echo "Fehler: 'jq' ist nicht installiert. Bitte installiere es (z.B. 'apt install jq' oder 'brew install jq')."
    exit 1
fi

# Prüfen, ob ein Ticker als Argument übergeben wurde
if [ -z "$1" ]; then
    echo "Verwendung: $0 <TICKER>"
    echo "Beispiel: $0 AAPL"
    exit 1
fi

TICKER="$1"
GATEWAY_URL="https://localhost:4002/v1/api"

# -------------------------------------------------------------------
# 1. Verbindung zum Client Portal Gateway prüfen
# -------------------------------------------------------------------
echo "Prüfe Gateway-Verbindung auf Port 4002..."
# Der Endpunkt /iserver/auth/status benötigt einen leeren Body, sonst 411
HTTP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$GATEWAY_URL/iserver/auth/status" -d "" -k)
if [ "$HTTP_STATUS" -ne 200 ]; then
    echo "Gateway antwortet nicht (HTTP $HTTP_STATUS)."
    echo "Stelle sicher, dass das Client Portal Gateway auf Port 4002 läuft und du eingeloggt bist."
    exit 1
fi

# -------------------------------------------------------------------
# 2. Contract ID (conid) für das Symbol ermitteln (secType=STK)
# -------------------------------------------------------------------
echo "Suche nach Symbol: $TICKER"
RESPONSE=$(curl -s -X POST "$GATEWAY_URL/iserver/secdef/search" \
    -H "Content-Type: application/json" \
    -d "{\"symbol\": \"$TICKER\", \"secType\": \"STK\"}" -k)

# conid aus der Antwort extrahieren (ersten Treffer nehmen)
CONID=$(echo "$RESPONSE" | jq -r '.[0].conid // empty')
if [ -z "$CONID" ]; then
    echo "Keine conid für $TICKER gefunden. Antwort der API:"
    echo "$RESPONSE"
    exit 1
fi
echo "Gefundene conid: $CONID"

# -------------------------------------------------------------------
# 3. Marktdaten abrufen (zwei Aufrufe nötig, da der erste nur 'pre-flight' ist)
#    Felder: 31 = letzter Preis | 87 = Tagesvolumen | 70 = Tageshoch | 71 = Tagestief
# -------------------------------------------------------------------
echo "Rufe Marktdaten ab..."

# Erster Aufruf (aktiviert den Stream)
curl -s "$GATEWAY_URL/iserver/marketdata/snapshot?conids=$CONID&fields=31,70,71,87" -k > /dev/null

# Kurze Pause, damit der Stream bereit ist
sleep 0.5

# Zweiter Aufruf – liefert die echten Daten
SNAPSHOT=$(curl -s "$GATEWAY_URL/iserver/marketdata/snapshot?conids=$CONID&fields=31,70,71,87" -k)

# Werte aus der JSON-Antwort extrahieren
LAST_PRICE=$(echo "$SNAPSHOT" | jq -r ".[0].fields[0] // \"N/A\"")
HIGH=$(echo "$SNAPSHOT" | jq -r ".[0].fields[1] // \"N/A\"")
LOW=$(echo "$SNAPSHOT" | jq -r ".[0].fields[2] // \"N/A\"")
VOLUME=$(echo "$SNAPSHOT" | jq -r ".[0].fields[3] // \"N/A\"")

# -------------------------------------------------------------------
# 4. Ausgabe
# -------------------------------------------------------------------
echo "----------------------------------------"
echo "Ticker      : $TICKER"
echo "Aktuell     : $LAST_PRICE"
echo "Volumen     : $VOLUME"
echo "Tageshoch   : $HIGH"
echo "Tagestief   : $LOW"
echo "----------------------------------------"