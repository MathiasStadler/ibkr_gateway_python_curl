#!/bin/bash
# start_vnc.sh - VNC-Browser mit Xvfb + fluxbox (Window Manager) + x11vnc
# Keine Abbrüche, Window-Management aktiv, Browser sichtbar

set +e

# Alte Lock-File/Prozesse killen (sauberer Neustart)
rm -rf /tmp/.X99-lock /tmp/.X11-unix/X99 /tmp/.X11-unix/X99 2>/dev/null || true
pkill -9 Xvfb 2>/dev/null || true
pkill -9 x11vnc 2>/dev/null || true
pkill -9 websockify 2>/dev/null || true
sleep 1

mkdir -p /tmp/.X11-unix
chown hermes:hermes /tmp/.X11-unix 2>/dev/null || true

echo "[INFO] Starte Xvfb auf :99 (mit XAUTH)"
Xvfb :99 -screen 0 1920x1080x24 +extension RANDR -auth /tmp/.Xauthority &
XVFB_PID=$!
export DISPLAY=:99
sleep 2

echo "[INFO] Starte fluxbox Window Manager (Desktop-Visualisierung)"
fluxbox &
FLUX_PID=$!
sleep 1

echo "[INFO] Starte x11vnc (Port 5901 - VNC-Desktop)"
# Starte x11vnc mit korrigierten Optionen für Docker-Umgebung
x11vnc -display :99 \
    -forever \
    -shared \
    -rfbport 5901 -nowf -ncache 10 \
    -nopw \
    -bg -o /tmp/x11vnc.log \
    -xkb \
    -noxrecord \
    -noxfixes \
    -noxdamage \
    -listen 0.0.0.0 &
sleep 2

echo "[INFO] Starte noVNC/Websockify (Port 6081)"
websockify --web=/usr/share/novnc/ 6081 localhost:5901 &
WEB_PID=$!
sleep 1

echo "[INFO] Starte Chromium (CDP auf 9222)"
chromium \
    --no-sandbox --disable-gpu --disable-dev-shm-usage \
    --remote-debugging-port=9222 --remote-debugging-address=0.0.0.0 \
    --window-size=1920,1080 --window-position=0,0 \
    --user-data-dir=/home/hermes/.chromium-data \
    --no-first-run --no-default-browser-check \
    "https://finance.yahoo.com" > /dev/null 2>&1 &
CHROME_PID=$!
sleep 2

echo "[INFO] Warte auf CDP-Start (Port 9222)..."
for i in $(seq 1 30); do
    if curl -s http://localhost:9222/json/version > /dev/null 2>&1; then
        echo "[OK] CDP bereit (Port 9222)"
        break
    fi
    sleep 1
done

echo "[INFO] JETZT SICHTBAR:"
echo "  Desktop Browser (mit Fenster-Manager): http://localhost:6081/vnc.html?host=localhost&port=6081"
echo "  VNC Client (nativ): vnc://localhost:5901"
echo "  CDP (Block-Dialog-Steuerung): http://localhost:9222/json/version"
echo "  Script Block-Dialog: docker exec browser-vnc sh /app/scripts/dismiss_dialogs.sh"

while true; do
    sleep 30
    if ! kill -0 $XVFB_PID 2>/dev/null; then
        echo "[WARN] Xvfb beendet, starte neu..."
        Xvfb :99 -screen 0 1920x1080x24 &
    fi
done