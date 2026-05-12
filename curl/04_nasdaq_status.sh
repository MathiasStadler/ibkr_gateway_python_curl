#!/bin/bash

# Prüfen, ob 'jq' installiert ist
if ! command -v jq &> /dev/null; then
    echo "Fehler: 'jq' ist nicht installiert. Bitte installiere es (z.B. 'sudo apt install jq' oder 'brew install jq')."
    exit 1
fi

# Globale Einstellungen
GATEWAY_URL="https://localhost:4002/v1/api"
TICKER="AAPL"  # Standardsymbol für NASDAQ

# -------------------------------------------------------------------
# Trading Schedule von IBKR über die API abrufen
# -------------------------------------------------------------------
get_trading_schedule() {
    local ticker="$1"
    local exchange="$2"
    
    # Schritt 1: conid für das Symbol ermitteln
    local search_response conid
    search_response=$(curl -s -X POST "$GATEWAY_URL/iserver/secdef/search" \
        -H "Content-Type: application/json" \
        -d "{\"symbol\": \"$ticker\", \"secType\": \"STK\"}" -k)
    conid=$(echo "$search_response" | jq -r '.[0].conid // empty')
    if [ -z "$conid" ]; then
        echo "❌ Fehler: Keine conid für $ticker gefunden" >&2
        return 1
    fi

    # Schritt 2: Trading Schedule von IBKR abrufen (die API liefert automatisch nur Handelstage)
    local schedule_response
    schedule_response=$(curl -s "$GATEWAY_URL/iserver/contract/trading-schedule?conid=$conid&exchange=$exchange" -k)
    if [ -z "$schedule_response" ] || echo "$schedule_response" | jq -e 'has("error")' >/dev/null 2>&1; then
        echo "❌ Fehler: Keine Trading Schedule für $ticker an $exchange gefunden" >&2
        return 1
    fi
    
    echo "$schedule_response"
}

# -------------------------------------------------------------------
# Hauptprogramm
# -------------------------------------------------------------------
echo "============================================"
echo "   NASDAQ Handelsstatus-Prüfung"
echo "============================================"

# Trading Schedule für NASDAQ abrufen
schedule_json=$(get_trading_schedule "$TICKER" "NASDAQ")
if [ $? -ne 0 ] || [ -z "$schedule_json" ] || [ "$schedule_json" = "null" ]; then
    echo "❌ Konnte keine Trading Schedule für NASDAQ abrufen."
    echo "   Bitte prüfe deine Gateway-Verbindung und dass du eingeloggt bist."
    exit 1
fi

# Aktuelles Datum im Format YYYY-MM-DD ermitteln
today=$(date '+%Y-%m-%d')

# Prüfen, ob das aktuelle Datum in der Trading Schedule vorkommt
# Die API liefert einen Eintrag in schedules nur für Handelstage
has_trading_day=$(echo "$schedule_json" | jq -r ".schedules[\"$today\"] != null")

if [ "$has_trading_day" = "true" ]; then
    # Es gibt einen Handelstag – Börse ist geöffnet oder wird geöffnet sein
    # Zusätzlich prüfen, ob die aktuellen Liquid Hours aktiv sind
    liquid_hours=$(echo "$schedule_json" | jq -r ".schedules[\"$today\"].liquid_hours[0] // empty")
    
    if [ -n "$liquid_hours" ] && [ "$liquid_hours" != "null" ]; then
        # Öffnungs- und Schließzeiten aus dem Unix-Timestamp extrahieren
        opening_ts=$(echo "$schedule_json" | jq -r ".schedules[\"$today\"].liquid_hours[0].opening")
        closing_ts=$(echo "$schedule_json" | jq -r ".schedules[\"$today\"].liquid_hours[0].closing")
        
        if [ -n "$opening_ts" ] && [ -n "$closing_ts" ] && [ "$opening_ts" != "null" ] && [ "$closing_ts" != "null" ]; then
            # Unix-Timestamps (in Sekunden) in lokale Zeit umwandeln
            open_time=$(date -d "@$opening_ts" '+%H:%M')
            close_time=$(date -d "@$closing_ts" '+%H:%M')
            
            # Aktuelle Zeit in Sekunden seit Mitternacht für Vergleich
            current_seconds=$(date '+%s')
            
            if [ $current_seconds -ge $opening_ts ] && [ $current_seconds -le $closing_ts ]; then
                echo "✅ MARKT IST GEÖFFNET (Reguläre Handelszeiten)"
                echo "   Öffnungszeit: $open_time Uhr (lokal)"
                echo "   Schließzeit: $close_time Uhr (lokal)"
            else
                echo "🌙 MARKT WIRD SPÄTER GEÖFFNET"
                echo "   Öffnungszeit: $open_time Uhr (lokal)"
                echo "   Schließzeit: $close_time Uhr (lokal)"
            fi
        else
            echo "✅ HEUTE IST EIN HANDELSTAG (Zeiten nicht verfügbar)"
        fi
    else
        echo "✅ HEUTE IST EIN HANDELSTAG"
    fi
else
    # Kein Eintrag in schedules gefunden → entweder Wochenende oder Feiertag
    # Zusätzliche Wochenendprüfung für genauere Meldung
    day_of_week=$(date '+%u')
    if [ "$day_of_week" -eq 6 ] || [ "$day_of_week" -eq 7 ]; then
        echo "⚠️  MARKT GESCHLOSSEN (Wochenende)"
    else
        echo "⚠️  MARKT GESCHLOSSEN (Feiertag oder kein Handelstag)"
    fi
fi

echo "============================================"
echo "Weitere Informationen zu den Handelszeiten:"
echo "• Reguläre Handelszeit: 09:30 – 16:00 ET"
echo "• Pre-Market: 04:00 – 09:30 ET"
echo "• After-Hours: 16:00 – 20:00 ET"
echo "============================================"