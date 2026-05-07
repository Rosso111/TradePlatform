#!/usr/bin/env python3
"""
Telegram Watcher — steuert tradeplatform.service wenn die App selbst down ist.
Wenn die App läuft, schläft der Watcher (App übernimmt alle Kommandos).
Wenn die App down ist, pollt der Watcher und wartet auf /start_bot.
"""
import json, os, subprocess, time, urllib.request

TOKEN   = os.environ.get('TELEGRAM_TOKEN', '')
CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID', '')
LAST_ID = 0


def send(text: str):
    url = f'https://api.telegram.org/bot{TOKEN}/sendMessage'
    payload = json.dumps({'chat_id': CHAT_ID, 'text': text, 'parse_mode': 'HTML'}).encode()
    try:
        urllib.request.urlopen(
            urllib.request.Request(url, data=payload, headers={'Content-Type': 'application/json'}),
            timeout=10,
        )
    except Exception as e:
        print(f'Telegram send error: {e}')


def is_app_running() -> bool:
    r = subprocess.run(['systemctl', 'is-active', 'tradeplatform'], capture_output=True, text=True)
    return r.stdout.strip() == 'active'


def systemctl(action: str, service: str) -> tuple[bool, str]:
    r = subprocess.run(['systemctl', action, service], capture_output=True, text=True, timeout=20)
    return r.returncode == 0, r.stderr.strip()


def handle(text: str):
    cmd = text.strip().lower().split()[0]
    if cmd in ('/start_bot', 'start_bot'):
        ok, err = systemctl('start', 'tradeplatform')
        send('🟢 <b>Bot gestartet.</b>' if ok else f'❌ Start fehlgeschlagen: {err}')
    elif cmd in ('/status_bot', 'status_bot'):
        lines = ['📡 <b>Service-Status (Watcher)</b>']
        for svc in ('tradeplatform', 'ibgateway'):
            r = subprocess.run(['systemctl', 'is-active', svc], capture_output=True, text=True)
            state = r.stdout.strip()
            lines.append(f'{"✅" if state == "active" else "❌"} {svc}: {state}')
        send('\n'.join(lines))


def get_updates_offset() -> int:
    """Holt den aktuellen Offset damit wir keine alten Nachrichten verarbeiten."""
    url = f'https://api.telegram.org/bot{TOKEN}/getUpdates?limit=1&offset=-1'
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read())
        results = data.get('result', [])
        if results:
            return results[-1]['update_id'] + 1
    except Exception:
        pass
    return 0


def poll_once(offset: int) -> int:
    url = f'https://api.telegram.org/bot{TOKEN}/getUpdates?offset={offset}&timeout=20'
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            data = json.loads(resp.read())
        for upd in data.get('result', []):
            offset = upd.get('update_id', offset) + 1
            msg = upd.get('message', {})
            if str(msg.get('chat', {}).get('id', '')) != CHAT_ID:
                continue
            text = msg.get('text', '').strip()
            if text:
                print(f'CMD: {text}')
                handle(text)
    except Exception as e:
        print(f'Poll error: {e}')
        time.sleep(5)
    return offset


if __name__ == '__main__':
    print('TG Watcher gestartet.')
    offset = get_updates_offset()

    while True:
        if is_app_running():
            # App läuft — Watcher schläft, App übernimmt alle Kommandos
            time.sleep(15)
            offset = get_updates_offset()  # Offset aktualisieren damit keine alten MSG kommen
        else:
            # App down — Watcher übernimmt Polling
            print('App down — Watcher aktiv.')
            send('⚠️ <b>Bot ist gestoppt.</b>\nSende /start_bot zum Starten.')
            while not is_app_running():
                offset = poll_once(offset)
            print('App läuft wieder — Watcher schläft.')
            offset = get_updates_offset()
