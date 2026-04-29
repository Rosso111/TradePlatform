#!/bin/bash
# Automatischer IB Gateway Login via xdotool

ENV_FILE="/home/martin/.openclaw/workspace/TradePlatform/.env"
DISPLAY_NUM=99
export DISPLAY=":${DISPLAY_NUM}"

log() { echo "[$(date '+%H:%M:%S')] $1" | tee -a /home/martin/ibgateway/gateway.log; }

# Credentials aus .env laden
IBKR_USERNAME=$(grep '^IBKR_USERNAME=' "$ENV_FILE" | cut -d'=' -f2-)
IBKR_PASSWORD=$(grep '^IBKR_PASSWORD=' "$ENV_FILE" | cut -d'=' -f2-)

if [ -z "$IBKR_USERNAME" ] || [ -z "$IBKR_PASSWORD" ]; then
    log "ERROR: IBKR_USERNAME oder IBKR_PASSWORD fehlt in .env"
    exit 1
fi

# Auf Login-Fenster warten
log "Warte auf IB Gateway Login-Dialog..."
for i in $(seq 1 30); do
    WIN_ID=$(xdotool search --name "IBKR-Gateway" 2>/dev/null | head -1)
    [ -n "$WIN_ID" ] && break
    sleep 2
done

if [ -z "$WIN_ID" ]; then
    log "ERROR: Gateway-Fenster nicht gefunden"
    exit 1
fi

log "Fenster gefunden: $WIN_ID"
sleep 3

# Fensterposition ermitteln
eval $(xdotool getwindowgeometry --shell "$WIN_ID" 2>/dev/null)
WIN_X=$X; WIN_Y=$Y; WIN_W=$WIDTH; WIN_H=$HEIGHT
log "Fenster: ${WIN_W}x${WIN_H} @ ${WIN_X},${WIN_Y}"

# Koordinaten berechnen (fenster-relative Offsets, empirisch ermittelt)
IB_API_X=$((WIN_X + 580))
IB_API_Y=$((WIN_Y + 186))
PAPER_X=$((WIN_X + 523))
PAPER_Y=$((WIN_Y + 221))
USER_X=$((WIN_X + 383))
USER_Y=$((WIN_Y + 271))
LOGIN_X=$((WIN_X + 395))
LOGIN_Y=$((WIN_Y + 440))

# IB API auswählen
log "Wähle IB API..."
xdotool windowraise "$WIN_ID"
sleep 0.5
xdotool mousemove $IB_API_X $IB_API_Y && xdotool click 1
sleep 0.5

# Paper-Trading auswählen (mehrere Versuche)
log "Wähle Paper-Trading..."
for offset in 0 30 -30 60 -60; do
    xdotool mousemove $((PAPER_X + offset)) $PAPER_Y && xdotool click 1
    sleep 0.2
done
sleep 0.5

# Username eingeben
log "Gebe Credentials ein..."
xdotool mousemove $USER_X $USER_Y && xdotool click 1
sleep 0.3
xdotool key ctrl+a
xdotool type --clearmodifiers --delay 50 "$IBKR_USERNAME"
sleep 0.3

# Tab zu Passwort
xdotool key Tab
sleep 0.3
xdotool type --clearmodifiers --delay 30 "$IBKR_PASSWORD"
sleep 0.5

# Login per Enter-Taste (zuverlässiger als Button-Klick)
log "Sende Enter für Paper-Login..."
xdotool key Return
sleep 2

# Paper-Trading Warnung akzeptieren
# Suche explizit nach dem "Warnung!" Dialog (nicht "GATEWAY" - das ist das falsche Fenster)
# Der Button wird per Return-Taste im Content-Window-Kind gesendet (xdotool click funktioniert ohne WM nicht)
log "Warte auf Paper-Trading Warnung..."
for i in $(seq 1 15); do
    WARN_ID=$(xdotool search --name "Warnung" 2>/dev/null | head -1)
    if [ -n "$WARN_ID" ]; then
        log "Warnung-Dialog gefunden, akzeptiere..."
        sleep 1
        # Content-Window-Kind des Dialogs für Return-Taste finden
        CONTENT_ID=$(xdotool search --name "Content window" 2>/dev/null | tail -1)
        if [ -n "$CONTENT_ID" ]; then
            xdotool windowraise "$WARN_ID"
            sleep 0.3
            xdotool windowfocus "$CONTENT_ID" 2>/dev/null || true
            sleep 0.5
            xdotool key --window "$CONTENT_ID" --clearmodifiers Return
        else
            # Fallback: Return direkt an Warnung-Fenster
            xdotool windowraise "$WARN_ID"
            xdotool windowfocus "$WARN_ID"
            sleep 0.5
            xdotool key --window "$WARN_ID" --clearmodifiers Return
        fi
        break
    fi
    sleep 2
done

# Auf Port 4002 warten
log "Warte auf API Port 4002..."
for i in $(seq 1 30); do
    if ss -tlnp | grep -q ':4002'; then
        log "Gateway bereit — Port 4002 offen!"
        exit 0
    fi
    sleep 2
done

log "ERROR: Port 4002 nicht geöffnet"
exit 1
