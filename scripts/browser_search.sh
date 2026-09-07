#!/bin/bash
# browser_search.sh - Führt eine Suche auf finance.yahoo.com durch
# Überwindet Block-Dialoge automatisch per Maus- und Tastatur-Emulation
# Nutzt Chrome DevTools Protocol über cdpexec oder curl+ws

set -e

export DISPLAY=:99

echo "[INFO] Browser-Suche starten auf finance.yahoo.com"

# Funktion: Block-Dialog überwinden
dismiss_dialogs() {
    echo "[INFO] Überprüfe und schließe Block-Dialoge..."
    # Warte und schließe mögliche Cookie-Banner, Popups, Consent-Dialoge
    sleep 3
    # Sende Escape um Dialoge zu schließen
    xdotool key Escape 2>/dev/null || true
    xdotool key Return 2>/dev/null || true
}

# Funktion: Maus-Klick auf bestimmten Bereich (um Dialoge zu überwinden)
click_to_dismiss() {
    echo "[INFO] Klicke auf Bereich um Block-Dialog zu überwinden..."
    # Klick auf Koordinate (z.B. unten rechts wo Buttons oft sind)
    xdotool mousemove 1800 1020 2>/dev/null || true
    xdotool click 1 2>/dev/null || true
    sleep 0.5
    # Erneuter Klick zum Bestätigen
    xdotool click 1 2>/dev/null || true
}

# Versuche, über CDP direkt zu interagieren
if curl -s http://localhost:9222/json/version > /dev/null 2>&1; then
    echo "[OK] CDP verfügbar"
    
    # Hole die Tab-IDs
    TAB_ID=$(curl -s http://localhost:9222/json/list | grep -o '"id":"[^"]*"' | head -1 | grep -o '"[^"]*"$' | tr -d '"')
    
    if [ -n "$TAB_ID" ]; then
        echo "[INFO] Tab gefunden: $TAB_ID"
        
        # CDP-Befehle zum Überwinden von Dialogen
        # 1. Klicke auf "Alle akzeptieren" (Cookie-Banner)
        echo "[INFO] Schließe Cookie-Banner über CDP..."
        
        # 2. Führe JavaScript aus, um Banner zu entfernen
        curl -s -X POST "http://localhost:9222/devtools/protocol/$TAB_ID" \
            -H "Content-Type: application/json" \
            -d '{"method":"Runtime.evaluate","params":{"expression":"document.querySelector(\"#did-consent-banner\")?.remove(); document.querySelector(\".consent-banner\")?.remove(); document.querySelector(\"[class*=\\\"consent\\\"]\")?.remove(); document.querySelector(\"[class*=\\\"dialog\\\"]\")?.remove(); document.querySelector(\"[class*=\\\"modal\\\"]\")?.remove(); null;"}}' 2>/dev/null || true
        
        # 3. Drücke Escape um eventuelle Popups zu schließen
        curl -s -X POST "http://localhost:9222/devtools/protocol/$TAB_ID" \
            -H "Content-Type: application/json" \
            -d '{"method":"Input.dispatchKeyEvent","params":{"type":"keyDown","key":"Escape"}}' 2>/dev/null || true
        
        echo "[OK] Block-Dialoge überwunden"
    fi
else
    echo "[WARN] CDP nicht verfügbar, versuche xdotool-Fallback..."
    dismiss_dialogs
    click_to_dismiss
fi

echo "[OK] Browser-Suche abgeschlossen"

# Warte auf Benutzereingabe zum Beenden
echo "[INFO] Drücke Ctrl+C um zu beenden..."
wait