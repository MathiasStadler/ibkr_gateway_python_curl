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
    
    # Prüfen, ob search_response gültiges JSON ist
    if ! echo "$search_response" | jq empty 2>/dev/null; then
        echo "❌ Fehler: Ungültige Antwort von secdef/search" >&2
        echo "Antwort: $search_response" >&2
        return 1
    fi
    
    conid=$(echo "$search_response" | jq -r '.[0].conid // empty')
    if [ -z "$conid" ]; then
        echo "❌ Fehler: Keine conid für $ticker gefunden" >&2
        return 1
    fi

    # Schritt 2: Trading Schedule von IBKR abrufen
    local schedule_response
    schedule_response=$(curl -s "$GATEWAY_URL/iserver/contract/trading-schedule?conid=$conid&exchange=$exchange" -k)
    
    # Prüfen, ob schedule_response gültiges JSON ist
    if ! echo "$schedule_response" | jq empty 2>/dev/null; then
        echo "❌ Fehler: Ungültige Antwort von trading-schedule" >&2
        echo "Antwort: $schedule_response" >&2
        return 1
    fi
    
    # Prüfen auf API-Fehler
    if echo "$schedule_response" | jq -e 'has("error")' >/dev/null 2>&1; then
        echo "❌ Fehler in API-Antwort: $(echo "$schedule_response" | jq -r '.error')" >&2
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
if [ $? -ne 0 ] || [ -z "$schedule_json" ]; then
    echo "⚠️  Konnte keine Trading Schedule von API abrufen."
    echo "   Verwende Fallback auf reguläre Handelszeiten (09:30-16:00 ET)."
    
    # Fallback: Keine API-Daten -> wir nutzen die lokale Zeit und schätzen
    # Aktuelle Uhrzeit in UTC und Umrechnung auf ET ist aufwendig. Vereinfacht:
    # Wir zeigen nur eine Meldung, dass der Status nicht ermittelt werden kann.
    echo "   Bitte prüfe Gateway-Verbindung und Login."
    exit 1
fi

# Aktuelles Datum im Format YYYY-MM-DD ermitteln (lokale Host-Zeit)
today=$(date '+%Y-%m-%d')

# Prüfen, ob das aktuelle Datum in der Trading Schedule vorkommt
has_trading_day=$(echo "$schedule_json" | jq -r ".schedules[\"$today\"] != null" 2>/dev/null)

if [ "$has_trading_day" = "true" ]; then
    liquid_hours=$(echo "$schedule_json" | jq -r ".schedules[\"$today\"].liquid_hours[0] // empty" 2>/dev/null)
    
    if [ -n "$liquid_hours" ] && [ "$liquid_hours" != "null" ] && [ "$liquid_hours" != "empty" ]; then
        # Unix-Timestamps (UTC) aus der API - als Zahlen, sicher extrahieren
        opening_ts=$(echo "$schedule_json" | jq -r ".schedules[\"$today\"].liquid_hours[0].opening // 0" 2>/dev/null)
        closing_ts=$(echo "$schedule_json" | jq -r ".schedules[\"$today\"].liquid_hours[0].closing // 0" 2>/dev/null)
        
        # Prüfen, ob die Werte gültige Zahlen sind (nicht 0 und nicht null)
        if [[ "$opening_ts" =~ ^[0-9]+$ ]] && [ "$opening_ts" -gt 0 ] && [[ "$closing_ts" =~ ^[0-9]+$ ]] && [ "$closing_ts" -gt 0 ]; then
            # Umwandlung in lokale Host-Zeit (MEZ/MESZ)
            open_local=$(date -d "@$opening_ts" '+%H:%M' 2>/dev/null)
            close_local=$(date -d "@$closing_ts" '+%H:%M' 2>/dev/null)
            
            # Aktuelle Zeit in Sekunden seit 1970 (UTC) für Vergleich
            current_utc=$(date -u '+%s')
            
            if [ $current_utc -ge $opening_ts ] && [ $current_utc -le $closing_ts ]; then
                echo "✅ MARKT IST GEÖFFNET"
            else
                echo "🌙 MARKT WIRD SPÄTER GEÖFFNET"
            fi
            echo "   Öffnungszeit (NASDAQ): $(date -d "@$opening_ts" '+%H:%M %Z' 2>/dev/null) ET"
            echo "   Öffnungszeit (lokal):  $open_local Uhr"
            echo "   Schließzeit (NASDAQ): $(date -d "@$closing_ts" '+%H:%M %Z' 2>/dev/null) ET"
            echo "   Schließzeit (lokal):  $close_local Uhr"
        else
            echo "✅ HEUTE IST EIN HANDELSTAG (Zeiten nicht verfügbar)"
        fi
    else
        echo "✅ HEUTE IST EIN HANDELSTAG"
    fi
else
    # Kein Eintrag in schedules → kein Handelstag (Wochenende/Feiertag)
    day_of_week=$(date '+%u')
    if [ "$day_of_week" -eq 6 ] || [ "$day_of_week" -eq 7 ]; then
        echo "⚠️  MARKT GESCHLOSSEN (Wochenende)"
    else
        echo "⚠️  MARKT GESCHLOSSEN (Feiertag oder kein Handelstag)"
    fi
fi

echo "============================================"
echo "Hinweis: Die Zeiten basieren auf der"
echo "Systemzeit dieses Hosts ($(date '+%Z'))."
echo "NASDAQ-Regelzeiten: 09:30 – 16:00 ET"
echo "============================================"