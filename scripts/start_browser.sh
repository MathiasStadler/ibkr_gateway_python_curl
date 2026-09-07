#!/bin/bash
# start_browser.sh - Startet Xvfb + Chromium mit Remote-Debugging
# Steuerung über Chrome DevTools Protocol (CDP) von außen möglich

set -e

echo "[INFO] Starte Xvfb auf Display :99"
Xvfb :99 -screen 0 1920x1080x24 +extension RANDR &
XVFB_PID=$!
export DISPLAY=:99
sleep 2

echo "[INFO] Starte Chromium mit Remote-Debugging auf Port 9222"
chromium \
    --no-sandbox \
    --disable-gpu \
    --disable-dev-shm-usage \
    --remote-debugging-port=9222 \
    --remote-debugging-address=0.0.0.0 \
    --window-size=1920,1080 \
    --window-position=0,0 \
    --user-data-dir=/home/hermes/.chromium-data \
    --no-first-run \
    --no-default-browser-check \
    "https://finance.yahoo.com" &
CHROME_PID=$!

echo "[INFO] Chromium PID: $CHROME_PID, Xvfb PID: $XVFB_PID"
echo "[INFO] Warte auf Chromium..."

# Warte bis CDP-Server bereit ist
for i in {1..30}; do
    if curl -s http://localhost:9222/json/version > /dev/null 2>&1; then
        echo "[OK] Chromium CDP ready on port 9222"
        break
    fi
    sleep 1
done

# Hole die WebSocket-Debugger-URL
echo "[INFO] CDP WebSocket-URLs:"
curl -s http://localhost:9222/json | head -50

echo "[INFO] Container läuft. CDP-Endpunkt: http://localhost:9222"

# Halte Container am Laufen
while true; do
    sleep 60
    if ! kill -0 $CHROME_PID 2>/dev/null; then
        echo "[WARN] Chromium beendet. Starte neu..."
        break
    fi
done

kill $CHROME_PID 2>/dev/null || true
kill $XVFB_PID 2>/dev/null || true