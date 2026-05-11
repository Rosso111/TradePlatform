import logging
import os
import threading
import urllib.request
import urllib.parse
import json
import time

log = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN', '')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID', '')

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

    if cmd in ('/stop_bot', 'stop_bot'):
        send_message('🔴 <b>Bot wird gestoppt…</b>\nSende /start_bot zum Starten (Watcher übernimmt).')
        import subprocess as _sp
        _sp.Popen(['systemctl', 'stop', 'tradeplatform'])

    elif cmd in ('/restart_bot', 'restart_bot'):
        send_message('🔄 <b>Bot wird neu gestartet…</b>')
        import subprocess as _sp
        _sp.Popen(['systemctl', 'restart', 'tradeplatform'])

    elif cmd in ('/status_bot', 'status_bot'):
        import subprocess as _sp
        lines = ['📡 <b>Service-Status</b>']
        for svc in ('tradeplatform', 'ibgateway'):
            r = _sp.run(['systemctl', 'is-active', svc], capture_output=True, text=True)
            state = r.stdout.strip()
            lines.append(f'{"✅" if state == "active" else "❌"} {svc}: {state}')
        from services.ibkr_connector import IBKRConnectionPool
        for g in IBKRConnectionPool.status():
            icon = '🟢' if g['connected'] else ('⏸' if g.get('circuit_breaker_active') else '🔴')
            lines.append(f'{icon} IBKR :{g["port"]}: {"verbunden" if g["connected"] else "getrennt"}')
        send_message('\n'.join(lines))

    elif cmd in ('/pause', 'pause', '/stopp', 'stopp'):
        try:
            from app import scheduler
            scheduler.pause_job('trading_cycle')
            send_message('⏸ <b>Trading pausiert.</b>\nKeine neuen Orders bis /weiter.')
        except Exception as e:
            send_message(f'❌ Fehler: {e}')

    elif cmd in ('/weiter', 'weiter', '/resume', 'resume'):
        try:
            from app import scheduler
            scheduler.resume_job('trading_cycle')
            send_message('▶️ <b>Trading fortgesetzt.</b>\nNächster Zyklus in max. 15 Min.')
        except Exception as e:
            send_message(f'❌ Fehler: {e}')

    elif cmd in ('/status', 'status'):
        _handle_command('/status_bot', app)

    elif cmd in ('/top10', 'top10'):
        with app.app_context():
            try:
                from datetime import date, timedelta
                import psycopg, os
                conn = psycopg.connect(
                    host=os.environ.get('POSTGRES_HOST', 'localhost'),
                    port=int(os.environ.get('POSTGRES_PORT', 5432)),
                    dbname=os.environ.get('POSTGRES_DB', 'Tradebot'),
                    user=os.environ.get('POSTGRES_USER', 'openclaw'),
                    password=os.environ.get('POSTGRES_PASSWORD', ''),
                    sslmode=os.environ.get('POSTGRES_SSLMODE', 'prefer'),
                )
                cur = conn.cursor()
                cur.execute(
                    "SELECT MAX(date) FROM signals WHERE action='BUY' AND date >= %s",
                    (date.today() - timedelta(days=5),)
                )
                sig_date = cur.fetchone()[0]
                if not sig_date:
                    send_message('📊 Keine aktuellen BUY-Signale.')
                    conn.close()
                    return
                cur.execute("""
                    SELECT st.symbol, st.name, sig.score, sig.rsi, p.close_eur, st.sector
                    FROM signals sig
                    JOIN stocks st ON st.id = sig.stock_id
                    LEFT JOIN (
                        SELECT DISTINCT ON (stock_id) stock_id, close_eur
                        FROM prices ORDER BY stock_id, date DESC
                    ) p ON p.stock_id = sig.stock_id
                    WHERE sig.action='BUY' AND sig.date=%s
                    ORDER BY sig.score DESC LIMIT 10
                """, (sig_date,))
                rows = cur.fetchall()
                conn.close()
                lines = [f'📊 <b>Top 10 Aktien — {sig_date}</b>']
                for i, (sym, name, score, rsi, eur, sector) in enumerate(rows, 1):
                    name_str = f' ({name[:20]})' if name else ''
                    lines.append(
                        f'{i}. <b>{sym}</b>{name_str}\n'
                        f'   Score {score:.0f}  RSI {(rsi or 0):.0f}  @€{(eur or 0):.2f}'
                        + (f'  <i>{sector[:14]}</i>' if sector else '')
                    )
                send_message('\n'.join(lines))
            except Exception as e:
                send_message(f'❌ Top10-Fehler: {e}')

    elif cmd in ('/topruns', 'topruns'):
        with app.app_context():
            from models import SimulationRun
            runs = SimulationRun.query.filter_by(status='completed').order_by(
                SimulationRun.total_return_pct.desc()
            ).limit(10).all()
            lines = ['🏆 <b>Top 10 Backtest-Runs:</b>']
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

    elif cmd in ('/portfolio', 'portfolio'):
        with app.app_context():
            import psycopg, os
            try:
                conn = psycopg.connect(
                    host=os.environ.get('POSTGRES_HOST', 'localhost'),
                    port=int(os.environ.get('POSTGRES_PORT', 5432)),
                    dbname=os.environ.get('POSTGRES_DB', 'Tradebot'),
                    user=os.environ.get('POSTGRES_USER', 'openclaw'),
                    password=os.environ.get('POSTGRES_PASSWORD', ''),
                    sslmode=os.environ.get('POSTGRES_SSLMODE', 'prefer'),
                )
                cur = conn.cursor()
                cur.execute("""
                    SELECT st.symbol, pos.shares, pos.entry_price_eur,
                           p.close_eur, pos.stop_loss
                    FROM positions pos
                    JOIN stocks st ON st.id = pos.stock_id
                    LEFT JOIN (
                        SELECT DISTINCT ON (stock_id) stock_id, close_eur
                        FROM prices ORDER BY stock_id, date DESC
                    ) p ON p.stock_id = pos.stock_id
                    ORDER BY pos.entry_price_eur * pos.shares DESC
                """)
                rows = cur.fetchall()
                conn.close()
                if not rows:
                    send_message('📂 Keine offenen Positionen.')
                    return
                lines = [f'📂 <b>Positionen ({len(rows)})</b>']
                for sym, shares, entry, curr, sl in rows:
                    curr = curr or entry
                    pnl = (curr - entry) * shares
                    pnl_str = f'{pnl:+.0f}€'
                    lines.append(f'• <b>{sym}</b> {shares:.1f}Stk @{entry:.2f}→{curr:.2f} {pnl_str}')
                send_message('\n'.join(lines))
            except Exception as e:
                send_message(f'❌ Portfolio-Fehler: {e}')

    elif cmd in ('/signals', 'signals'):
        with app.app_context():
            try:
                from datetime import date, timedelta
                import psycopg, os
                conn = psycopg.connect(
                    host=os.environ.get('POSTGRES_HOST', 'localhost'),
                    port=int(os.environ.get('POSTGRES_PORT', 5432)),
                    dbname=os.environ.get('POSTGRES_DB', 'Tradebot'),
                    user=os.environ.get('POSTGRES_USER', 'openclaw'),
                    password=os.environ.get('POSTGRES_PASSWORD', ''),
                    sslmode=os.environ.get('POSTGRES_SSLMODE', 'prefer'),
                )
                cur = conn.cursor()
                cur.execute(
                    "SELECT MAX(date) FROM signals WHERE action='BUY' AND date >= %s",
                    (date.today() - timedelta(days=5),)
                )
                sig_date = cur.fetchone()[0]
                if not sig_date:
                    send_message('📊 Keine aktuellen Signale.')
                    conn.close()
                    return
                cur.execute("""
                    SELECT st.symbol, sig.score, sig.rsi, p.close_eur
                    FROM signals sig JOIN stocks st ON st.id = sig.stock_id
                    LEFT JOIN (
                        SELECT DISTINCT ON (stock_id) stock_id, close_eur
                        FROM prices ORDER BY stock_id, date DESC
                    ) p ON p.stock_id = sig.stock_id
                    WHERE sig.action='BUY' AND sig.date=%s
                    ORDER BY sig.score DESC LIMIT 8
                """, (sig_date,))
                rows = cur.fetchall()
                conn.close()
                lines = [f'📊 <b>BUY-Signale {sig_date}</b>']
                for sym, score, rsi, eur in rows:
                    lines.append(f'• <b>{sym}</b> Score {score:.0f} RSI {(rsi or 0):.0f} @€{(eur or 0):.2f}')
                send_message('\n'.join(lines))
            except Exception as e:
                send_message(f'❌ Signal-Fehler: {e}')

    elif cmd in ('/cash', 'cash'):
        with app.app_context():
            import psycopg, os
            try:
                conn = psycopg.connect(
                    host=os.environ.get('POSTGRES_HOST', 'localhost'),
                    port=int(os.environ.get('POSTGRES_PORT', 5432)),
                    dbname=os.environ.get('POSTGRES_DB', 'Tradebot'),
                    user=os.environ.get('POSTGRES_USER', 'openclaw'),
                    password=os.environ.get('POSTGRES_PASSWORD', ''),
                    sslmode=os.environ.get('POSTGRES_SSLMODE', 'prefer'),
                )
                cur = conn.cursor()
                cur.execute("""
                    SELECT port.name, acc.cash_eur
                    FROM accounts acc
                    JOIN portfolios port ON port.id = acc.portfolio_id
                    ORDER BY port.id
                """)
                rows = cur.fetchall()
                conn.close()
                if not rows:
                    send_message('💰 Keine Konten gefunden.')
                    return
                lines = ['💰 <b>Verfügbares Cash</b>']
                for name, cash in rows:
                    lines.append(f'• <b>{name}</b>: €{(cash or 0):,.0f}')
                send_message('\n'.join(lines))
            except Exception as e:
                send_message(f'❌ Cash-Fehler: {e}')

    elif cmd in ('/pnl', 'pnl'):
        with app.app_context():
            import psycopg, os
            from datetime import date as _date
            try:
                conn = psycopg.connect(
                    host=os.environ.get('POSTGRES_HOST', 'localhost'),
                    port=int(os.environ.get('POSTGRES_PORT', 5432)),
                    dbname=os.environ.get('POSTGRES_DB', 'Tradebot'),
                    user=os.environ.get('POSTGRES_USER', 'openclaw'),
                    password=os.environ.get('POSTGRES_PASSWORD', ''),
                    sslmode=os.environ.get('POSTGRES_SSLMODE', 'prefer'),
                )
                cur = conn.cursor()
                cur.execute("""
                    SELECT port.name,
                           SUM((COALESCE(pr.close_eur, pos.entry_price_eur) - pos.entry_price_eur) * pos.shares) AS unrealized,
                           COUNT(pos.id) AS pos_count
                    FROM positions pos
                    JOIN portfolios port ON port.id = pos.portfolio_id
                    LEFT JOIN (
                        SELECT DISTINCT ON (stock_id) stock_id, close_eur
                        FROM prices ORDER BY stock_id, date DESC
                    ) pr ON pr.stock_id = pos.stock_id
                    GROUP BY port.id, port.name
                    ORDER BY port.id
                """)
                unrealized_rows = cur.fetchall()
                cur.execute("""
                    SELECT port.name, COALESCE(SUM(t.pnl_eur), 0)
                    FROM trades t
                    JOIN portfolios port ON port.id = t.portfolio_id
                    WHERE t.action = 'SELL' AND DATE(t.executed_at) = %s
                    GROUP BY port.id, port.name
                """, (_date.today(),))
                realized_map = {name: pnl for name, pnl in cur.fetchall()}
                conn.close()
                lines = [f'📊 <b>P&amp;L Übersicht ({_date.today()})</b>']
                for name, unrealized, pos_count in unrealized_rows:
                    unr = unrealized or 0
                    real = realized_map.get(name, 0) or 0
                    total = unr + real
                    emoji = '🟢' if total >= 0 else '🔴'
                    lines.append(
                        f'\n{emoji} <b>{name}</b>\n'
                        f'  Unrealisiert: €{unr:+,.0f} ({pos_count} Pos.)\n'
                        f'  Heute realisiert: €{real:+,.0f}\n'
                        f'  Gesamt: €{total:+,.0f}'
                    )
                send_message('\n'.join(lines))
            except Exception as e:
                send_message(f'❌ P&L-Fehler: {e}')

    elif cmd in ('/sell',) and len(text.strip().split()) > 1:
        symbol = text.strip().split()[1].upper()
        with app.app_context():
            import psycopg, os
            try:
                conn = psycopg.connect(
                    host=os.environ.get('POSTGRES_HOST', 'localhost'),
                    port=int(os.environ.get('POSTGRES_PORT', 5432)),
                    dbname=os.environ.get('POSTGRES_DB', 'Tradebot'),
                    user=os.environ.get('POSTGRES_USER', 'openclaw'),
                    password=os.environ.get('POSTGRES_PASSWORD', ''),
                    sslmode=os.environ.get('POSTGRES_SSLMODE', 'prefer'),
                )
                cur = conn.cursor()
                cur.execute("""
                    SELECT pos.id, port.name, pos.shares, pos.entry_price_eur,
                           COALESCE(pr.close_eur, pos.entry_price_eur) AS curr_eur
                    FROM positions pos
                    JOIN stocks st ON st.id = pos.stock_id
                    JOIN portfolios port ON port.id = pos.portfolio_id
                    LEFT JOIN (
                        SELECT DISTINCT ON (stock_id) stock_id, close_eur
                        FROM prices ORDER BY stock_id, date DESC
                    ) pr ON pr.stock_id = pos.stock_id
                    WHERE st.symbol = %s
                    ORDER BY port.id
                """, (symbol,))
                rows = cur.fetchall()
                conn.close()
                if not rows:
                    send_message(f'❌ Keine offene Position: <b>{symbol}</b>')
                    return
                if len(rows) > 1:
                    lines = [f'⚠️ <b>{symbol}</b> in mehreren Portfolios — welches?']
                    for pos_id, port_name, shares, entry, curr in rows:
                        pnl = (curr - entry) * shares
                        lines.append(f'• {port_name}: {shares:.0f} Stk  P&amp;L {pnl:+.0f}€')
                    send_message('\n'.join(lines))
                    return
                pos_id, port_name, shares, entry, curr = rows[0]
                pnl_est = (curr - entry) * shares
                send_message(f'⏳ Verkaufe <b>{symbol}</b> ({shares:.0f} Stk) aus {port_name}…\nErwartet: {pnl_est:+.0f}€')
                from models import Position
                from services.data_fetcher import fetch_exchange_rates
                from services.live_runner import execute_live_sell
                pos = Position.query.get(pos_id)
                fx_rates = fetch_exchange_rates()
                ok, msg = execute_live_sell(pos, fx_rates, reason='Telegram /sell')
                if ok:
                    send_message(f'✅ <b>{symbol}</b> verkauft\n{msg[:120]}')
                else:
                    send_message(f'❌ <b>{symbol}</b> fehlgeschlagen\n{msg[:120]}')
            except Exception as e:
                send_message(f'❌ Sell-Fehler: {e}')

    elif cmd in ('/help', 'help'):
        send_message(
            '📖 <b>Kommandos:</b>\n\n'
            '🔴 /pause — Trading stoppen\n'
            '🟢 /weiter — Trading fortsetzen\n'
            '📡 /status — System- &amp; IBKR-Status\n'
            '📂 /portfolio — offene Positionen\n'
            '💰 /cash — verfügbares Kapital\n'
            '📊 /pnl — P&amp;L heute (unrealisiert + realisiert)\n'
            '📊 /signals — aktuelle BUY-Signale\n'
            '📈 /top10 — Top 10 Aktien heute (Score/RSI/Kurs)\n'
            '🏆 /topruns — beste 10 Backtest-Runs\n'
            '💸 /sell SYMBOL — Position manuell verkaufen\n\n'
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

    # "bester run" / "top ergebnisse" — nur bei expliziter Backtest-Anfrage
    if re.search(r'bester?\s+run|top\s+run|beste\s+backtest|top\s+ergebnis', t, re.IGNORECASE):
        with app.app_context():
            from models import SimulationRun
            runs = SimulationRun.query.filter_by(status='completed').order_by(
                SimulationRun.total_return_pct.desc()
            ).limit(5).all()
            lines = ['🏆 <b>Top 5 Runs:</b>']
            for i, r in enumerate(runs, 1):
                lines.append(f"{i}. +{r.total_return_pct:.1f}% Sharpe {r.sharpe_ratio:.3f} | {r.name[:25]}")
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


def notify_signals(signals: list):
    """Morgen-Zusammenfassung der BUY-Signale via Telegram."""
    buys = [s for s in signals if s.get('action') == 'BUY']
    if not buys:
        send_message('📊 <b>Tages-Signale</b>\nHeute keine BUY-Signale.')
        return
    lines = [f'📊 <b>Tages-Signale — {len(buys)} BUY</b>']
    for s in buys[:10]:
        rsi = s.get('rsi') or 0
        eur = s.get('current_price_eur') or 0
        lines.append(
            f'• <b>{s["symbol"]}</b>  Score {s["score"]:.0f}  RSI {rsi:.0f}  @€{eur:.2f}  {(s.get("sector") or "")[:15]}'
        )
    send_message('\n'.join(lines))


def notify_trade(action: str, symbol: str, qty: int, price_eur: float,
                 pnl_eur: float | None = None, portfolio_name: str = ''):
    """Bestätigung eines ausgeführten IBKR-Trades via Telegram."""
    port_str = f' [{portfolio_name}]' if portfolio_name else ''
    if action == 'BUY':
        total = qty * price_eur
        msg = (f'🟢 <b>KAUF{port_str}</b>\n'
               f'{symbol}: {qty} Stk @ €{price_eur:.2f}\n'
               f'Investiert: €{total:,.0f}')
    else:
        pnl_str = f'\nP&amp;L: {pnl_eur:+.0f} EUR' if pnl_eur is not None else ''
        emoji = '🟡' if (pnl_eur or 0) >= 0 else '🔴'
        msg = (f'{emoji} <b>VERKAUF{port_str}</b>\n'
               f'{symbol}: {qty} Stk @ €{price_eur:.2f}{pnl_str}')
    send_message(msg)


def notify_buy_batch(buys: list, account_cash_eur: float, portfolio_name: str = ''):
    """
    Sammel-Benachrichtigung für mehrere Käufe in einem Zyklus.
    buys = list of (symbol, qty, price_eur, total_eur)
    """
    if not buys:
        return
    port_str = f' [{portfolio_name}]' if portfolio_name else ''
    total_invested = sum(b[3] for b in buys)
    lines = [f'🟢 <b>{len(buys)} Käufe{port_str}</b> | Investiert: €{total_invested:,.0f} | Cash noch: €{account_cash_eur:,.0f}']
    for symbol, qty, price_eur, total_eur in buys[:15]:
        lines.append(f'• {symbol}: {qty} Stk @ €{price_eur:.2f} (€{total_eur:,.0f})')
    if len(buys) > 15:
        lines.append(f'… und {len(buys) - 15} weitere Käufe')
    send_message('\n'.join(lines))


def notify_live_cycle(actions: list, portfolio_names: list):
    """Zusammenfassung eines abgeschlossenen Live-Zyklus (nur bei Aktionen)."""
    if not actions:
        return
    lines = [f'⚡ <b>Live-Zyklus abgeschlossen</b> ({len(actions)} Aktionen)']
    for a in actions[:6]:
        lines.append(f'• {a[:80]}')
    if len(actions) > 6:
        lines.append(f'… und {len(actions) - 6} weitere')
    send_message('\n'.join(lines))
