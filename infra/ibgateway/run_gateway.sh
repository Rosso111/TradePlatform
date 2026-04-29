#!/bin/bash
# Hauptskript für Systemd: startet Xvfb + Gateway + Auto-Login und bleibt aktiv

DISPLAY_NUM=99
LOG=/home/martin/ibgateway/gateway.log
export DISPLAY=":${DISPLAY_NUM}"

log() { echo "[$(date '+%H:%M:%S')] $1" | tee -a "$LOG"; }

log "=== IB Gateway Startup ==="

# Aufräumen (nur Java-Gateway-Prozess killen, nicht dieses Skript)
pkill -f "ibgateway/ibgateway" 2>/dev/null || true
pkill -f "Xvfb :${DISPLAY_NUM}" 2>/dev/null || true
rm -f "/tmp/.X${DISPLAY_NUM}-lock"
sleep 1

# Xvfb starten
log "Starte Xvfb..."
Xvfb ":${DISPLAY_NUM}" -screen 0 1280x1024x24 -ac &
XVFB_PID=$!
sleep 3

if ! kill -0 $XVFB_PID 2>/dev/null; then
    log "ERROR: Xvfb konnte nicht gestartet werden"
    exit 1
fi
log "Xvfb PID: $XVFB_PID"

# IB Gateway starten
log "Starte IB Gateway..."
DISPLAY=":${DISPLAY_NUM}" /home/martin/ibgateway/ibgateway >> "$LOG" 2>&1 &
GW_PID=$!
log "Gateway PID: $GW_PID"
sleep 8

# Auto-Login ausführen
log "Starte Auto-Login..."
DISPLAY=":${DISPLAY_NUM}" bash /home/martin/ibgateway/auto_login.sh

if [ $? -ne 0 ]; then
    log "ERROR: Auto-Login fehlgeschlagen"
    kill $GW_PID $XVFB_PID 2>/dev/null
    exit 1
fi

log "Gateway läuft. Überwache Prozess $GW_PID..."

# Gateway-Prozess überwachen (Systemd weiß wenn dieser stirbt)
wait $GW_PID
EXIT_CODE=$?
log "Gateway beendet mit Code $EXIT_CODE"

kill $XVFB_PID 2>/dev/null
exit $EXIT_CODE
