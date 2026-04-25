import logging
import threading
import urllib.request
import urllib.parse
import json
import time

log = logging.getLogger(__name__)

TELEGRAM_TOKEN = '***ROTATED***'
TELEGRAM_CHAT_ID = '8787363623'

_polling_thread = None
_last_update_id = 0


def send_message(text: str) -> bool:
    url = f'https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage'
    payload = json.dumps({'chat_id': TELEGRAM_CHAT_ID, 'text': text, 'parse_mode': 'HTML'}).encode()
    req = urllib.request.Request(url, data=payload, headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except Exception as e:
        log.warning('Telegram send failed: %s', e)
        return False


def _get_updates(offset=0):
    url = f'https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates?offset={offset}&timeout=30'
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=40) as resp:
            return json.loads(resp.read())
    except Exception as e:
        log.debug('Telegram getUpdates error: %s', e)
        return None


def _enqueue_message(text: str):
    """Schreibt eingehende Nachricht in Queue-Datei für Claude Code."""
    import os
    queue_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'tmp', 'telegram_queue.json')
    os.makedirs(os.path.dirname(queue_file), exist_ok=True)
    try:
        try:
            with open(queue_file) as f:
                queue = json.load(f)
        except Exception:
            queue = []
        queue.append({'text': text, 'ts': time.time(), 'processed': False})
        with open(queue_file, 'w') as f:
            json.dump(queue, f, ensure_ascii=False)
    except Exception as e:
        log.warning('Queue write failed: %s', e)


def _handle_command(text: str, app):
    cmd = text.strip().lower().split()[0] if text.strip() else ''

    if cmd in ('/status', 'status'):
        with app.app_context():
            from services.scenario_store import list_scenario_batches
            batches = list_scenario_batches()
            running = [b for b in batches if b.get('status') == 'running']
            if running:
                lines = ['⏳ <b>Laufende Batches:</b>']
                for b in running:
                    idx = b.get('current_index', 0)
                    total = len(b.get('scenario_ids', []))
                    lines.append(f"• {b.get('name', b['id'])}: {idx}/{total}")
                send_message('\n'.join(lines))
            else:
                send_message('✅ Kein Batch läuft gerade.')

    elif cmd in ('/top10', 'top10'):
        with app.app_context():
            from models import SimulationRun
            runs = SimulationRun.query.filter_by(status='completed').order_by(
                SimulationRun.total_return_pct.desc()
            ).limit(10).all()
            lines = ['🏆 <b>Top 10 Runs:</b>']
            for i, r in enumerate(runs, 1):
                lines.append(f"{i}. +{r.total_return_pct:.1f}% | Sharpe {r.sharpe_ratio:.3f} | {r.name[:28]}")
            send_message('\n'.join(lines))

    elif cmd in ('/batches', 'batches'):
        with app.app_context():
            from services.scenario_store import list_scenario_batches
            batches = list_scenario_batches()
            lines = ['📋 <b>Letzte Batches:</b>']
            icons = {'completed': '✅', 'running': '⏳', 'failed': '❌', 'pending': '⏸'}
            for b in batches[-8:]:
                icon = icons.get(b.get('status', ''), '❓')
                lines.append(f"{icon} {b.get('name', b['id'])}")
            send_message('\n'.join(lines))

    elif cmd in ('/start',) and len(text.strip().split()) > 1:
        batch_id = text.strip().split()[1]
        with app.app_context():
            from services.scenario_store import get_scenario_batch
            batch = get_scenario_batch(batch_id)
            if not batch:
                send_message(f'❌ Batch <code>{batch_id}</code> nicht gefunden.')
                return
            if batch.get('status') == 'running':
                send_message('⏳ Batch läuft bereits.')
                return
        req = urllib.request.Request(
            f'http://localhost:5000/api/scenario-batches/{batch_id}/run',
            data=b'{}', headers={'Content-Type': 'application/json'}
        )
        try:
            urllib.request.urlopen(req, timeout=10)
            send_message(f'🚀 Batch <code>{batch_id}</code> gestartet!')
        except Exception as e:
            send_message(f'❌ Fehler: {e}')

    elif cmd in ('/help', 'help'):
        send_message(
            '📖 <b>Schnell-Kommandos (sofort):</b>\n'
            '/status — laufende Batches\n'
            '/top10 — beste 10 Runs\n'
            '/batches — alle Batches\n'
            '/start &lt;batch_id&gt; — Batch starten\n\n'
            '💬 <b>Alles andere</b> → Claude verarbeitet (~60s)'
        )

    else:
        # Komplexe Anfrage → Worker-Thread (sofort, kein 60s-Delay)
        threading.Thread(target=_process_complex, args=(text, app), daemon=True).start()


def _process_complex(text: str, app):
    import re
    t = text.strip()

    # "sweep mit pos=X" oder "pos=X sweep"
    m = re.search(r'pos\s*[=:]\s*(\d+)', t, re.IGNORECASE)
    if m and re.search(r'sweep|scan|test|start', t, re.IGNORECASE):
        pos = int(m.group(1))
        _run_quick_sweep({'max_positions': pos}, f'pos={pos}', app)
        return

    # "sweep mit mps=X%" oder "max_position_size=X"
    m = re.search(r'mps\s*[=:]\s*(\d+)', t, re.IGNORECASE)
    if m and re.search(r'sweep|scan|test|start', t, re.IGNORECASE):
        mps = int(m.group(1)) / 100
        _run_quick_sweep({'max_position_size': mps}, f'mps={int(mps*100)}%', app)
        return

    # "bester run" / "top ergebnisse"
    if re.search(r'best|top|ergebn|result', t, re.IGNORECASE):
        with app.app_context():
            from models import SimulationRun
            runs = SimulationRun.query.filter_by(status='completed').order_by(
                SimulationRun.total_return_pct.desc()
            ).limit(5).all()
            lines = ['🏆 <b>Top 5:</b>']
            for i, r in enumerate(runs, 1):
                lines.append(f"{i}. +{r.total_return_pct:.1f}% Sharpe {r.sharpe_ratio:.3f}")
            send_message('\n'.join(lines))
        return

    # Fallback → Queue für Claude
    _enqueue_message(text)
    send_message(f'📨 An Claude weitergeleitet:\n<code>{text[:80]}</code>\n⏱ ~60s')


def _run_quick_sweep(param_override: dict, label: str, app):
    import json as _json, time as _time
    from services.scenario_store import get_all_scenarios, save_all_data

    base_params = {
        'persist_chunk_days': 4000,
        'cancel_check_interval_days': 100,
        'decision_log_mode': 'normal',
        'trailing_stop_pct': 0.08,
        'atr_position_sizing': False,
        'max_positions': 13,
        'top_n_signals': 7,
        'max_position_size': 0.30,
        'buy_threshold': 58.0,
        'sell_threshold': 38.0,
    }
    base_params.update(param_override)

    ts = int(_time.time())
    scenario_id = f'tg_quick_{ts}'
    batch_id = f'batch_tg_{ts}'

    scenario = {
        'id': scenario_id,
        'name': f'TG Quick Sweep — {label} — {ts}',
        'strategy_id': 'trend_quality_aggressive_v1',
        'universe_name': 'global_core_10y',
        'start_date': '2016-01-01',
        'end_date': '2026-04-22',
        'initial_capital_eur': 10000,
        'notes': f'Telegram quick sweep: {label}',
        'params_override': base_params,
    }

    import os
    data_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'scenarios.json')
    with open(data_file) as f:
        d = _json.load(f)
    d['scenarios'].append(scenario)
    d['scenario_batches'].append({
        'id': batch_id,
        'name': f'TG Quick — {label}',
        'scenario_ids': [scenario_id],
        'status': 'pending',
    })
    with open(data_file, 'w') as f:
        _json.dump(d, f, indent=2, ensure_ascii=False)

    req = urllib.request.Request(
        f'http://localhost:5000/api/scenario-batches/{batch_id}/run',
        data=b'{}', headers={'Content-Type': 'application/json'}
    )
    try:
        urllib.request.urlopen(req, timeout=10)
        send_message(f'🚀 Quick Sweep gestartet: <b>{label}</b>\nNotification wenn fertig.')
    except Exception as e:
        send_message(f'❌ Fehler beim Start: {e}')


def _polling_loop(app):
    global _last_update_id
    log.info('Telegram polling gestartet.')
    while True:
        try:
            data = _get_updates(offset=_last_update_id + 1)
            if data and data.get('ok'):
                for update in data.get('result', []):
                    uid = update.get('update_id', 0)
                    if uid > _last_update_id:
                        _last_update_id = uid
                    msg = update.get('message', {})
                    chat_id = str(msg.get('chat', {}).get('id', ''))
                    if chat_id != TELEGRAM_CHAT_ID:
                        continue
                    text = msg.get('text', '')
                    if text:
                        log.info('Telegram command: %s', text)
                        try:
                            _handle_command(text, app)
                        except Exception as e:
                            log.exception('Telegram command error: %s', e)
                            send_message(f'❌ Fehler: {e}')
        except Exception as e:
            log.debug('Polling error: %s', e)
            time.sleep(5)


def start_polling(app):
    global _polling_thread
    if _polling_thread and _polling_thread.is_alive():
        return
    _polling_thread = threading.Thread(
        target=_polling_loop,
        args=(app,),
        name='telegram-polling',
        daemon=True,
    )
    _polling_thread.start()
    log.info('Telegram polling thread gestartet.')


def notify_batch_complete(batch_name: str, results: list):
    if not results:
        send_message(f'✅ <b>{batch_name}</b>\nKeine Ergebnisse.')
        return
    best = max(results, key=lambda r: r.get('total_return_pct') or 0)
    worst = min(results, key=lambda r: r.get('total_return_pct') or 0)
    avg = sum(r.get('total_return_pct') or 0 for r in results) / len(results)
    msg = (
        f'✅ <b>{batch_name}</b> fertig ({len(results)} Runs)\n'
        f'🏆 Bester: +{best.get("total_return_pct", 0):.1f}% '
        f'(Sharpe {best.get("sharpe_ratio", 0):.3f})\n'
        f'📊 Durchschnitt: +{avg:.1f}%\n'
        f'📉 Schlechtester: +{worst.get("total_return_pct", 0):.1f}%'
    )
    send_message(msg)


def notify_run_complete(run_name: str, total_return_pct: float, sharpe: float, max_dd: float):
    emoji = '🟢' if total_return_pct > 0 else '🔴'
    msg = (
        f'{emoji} <b>{run_name}</b>\n'
        f'Return: {total_return_pct:+.2f}%\n'
        f'Sharpe: {sharpe:.3f} | MaxDD: {max_dd:.1f}%'
    )
    send_message(msg)
