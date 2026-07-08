"""systemd-Integration: sd_notify (READY/WATCHDOG) ohne Zusatz-Dependency.

Der Watchdog-Thread pingt systemd nur, solange der lokale /health-Endpoint
mit 200 antwortet — hängt der HTTP-Server oder die DB, bleiben die Pings
aus und systemd startet den Dienst nach WatchdogSec neu (Felix-8).
"""
import logging
import os
import socket
import threading

import requests

log = logging.getLogger(__name__)


def sd_notify(message: str) -> None:
    """Schickt eine Nachricht an den systemd-Notify-Socket (no-op ohne systemd)."""
    addr = os.environ.get('NOTIFY_SOCKET')
    if not addr:
        return
    if addr.startswith('@'):
        addr = '\0' + addr[1:]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
            sock.sendto(message.encode(), addr)
    except OSError as e:
        log.warning("sd_notify fehlgeschlagen: %s", e)


def start_watchdog(port: int) -> None:
    """Startet den Watchdog-Ping-Thread, falls systemd WatchdogSec gesetzt hat."""
    usec = os.environ.get('WATCHDOG_USEC')
    if not usec:
        log.info("Kein WATCHDOG_USEC gesetzt — systemd-Watchdog inaktiv.")
        return
    interval = max(int(usec) / 1_000_000 / 2, 5)
    url = f'http://127.0.0.1:{port}/health'

    def ping_loop():
        while True:
            try:
                resp = requests.get(url, timeout=10)
                if resp.status_code == 200:
                    sd_notify('WATCHDOG=1')
                else:
                    log.error("Watchdog: /health lieferte %s — kein Ping.", resp.status_code)
            except requests.RequestException as e:
                log.error("Watchdog: /health nicht erreichbar (%s) — kein Ping.", e)
            threading.Event().wait(interval)

    thread = threading.Thread(target=ping_loop, name='sd-watchdog', daemon=True)
    thread.start()
    log.info("systemd-Watchdog aktiv: Ping alle %.0fs auf %s", interval, url)
