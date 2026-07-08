"""Scenario-Batch-Runner — gemeinsam genutzt von API-Route und Telegram-Bot.

VANCE-M3: Der Telegram-Bot rief die Run-Route früher per HTTP auf
localhost:5000 auf und scheiterte lautlos an @login_required. Route und
Bot rufen jetzt beide start_batch() direkt auf.
"""
import json
import logging
import re
import threading
from datetime import datetime, timezone

from models import db
from services.scenario_store import get_scenario, get_scenario_batch, update_scenario_batch

log = logging.getLogger(__name__)


def start_batch(app, batch_id):
    """Startet einen Scenario-Batch im Hintergrund-Thread.

    Returns (ok, error): (True, None) bei Start, sonst (False, Fehlermeldung).
    """
    batch = get_scenario_batch(batch_id)
    if not batch:
        return False, 'Batch nicht gefunden'
    if str(batch.get('status')).lower() == 'running':
        return False, 'Batch läuft bereits'

    def background_batch_runner():
        from services.replay_engine import create_simulation_run, run_historical_replay
        try:
            current_batch = update_scenario_batch(batch_id, {
                'status': 'running',
                'started_at': datetime.now(timezone.utc).isoformat(),
                'error': None,
            })
            run_ids = list(current_batch.get('run_ids') or [])
            results = list(current_batch.get('results') or [])

            for index, scenario_id in enumerate(current_batch.get('scenario_ids') or []):
                scenario = get_scenario(scenario_id)
                if not scenario:
                    raise ValueError(f'Szenario {scenario_id} nicht gefunden')

                payload = {
                    'name': scenario.get('name') or scenario_id,
                    'start_date': scenario.get('start_date'),
                    'end_date': scenario.get('end_date'),
                    'initial_capital_eur': scenario.get('initial_capital_eur', 10000),
                    'strategy_id': scenario.get('strategy_id'),
                    'universe_name': scenario.get('universe_name'),
                    'notes': scenario.get('notes'),
                    'auto_start': False,
                    'strategy_params_override': scenario.get('params_override') or {},
                }

                with app.app_context():
                    run = create_simulation_run(payload)
                    run_historical_replay(app, run.id)
                    db.session.refresh(run)

                _tax = {}
                if run.notes:
                    _m = re.search(r'tax_summary=(\{.*\})', run.notes)
                    if _m:
                        try:
                            _tax = json.loads(_m.group(1))
                        except Exception:
                            pass

                run_ids.append(run.id)
                results.append({
                    'scenario_id': scenario_id,
                    'run_id': run.id,
                    'status': run.status,
                    'final_equity_eur': run.final_equity_eur,
                    'total_return_pct': run.total_return_pct,
                    'max_drawdown_pct': run.max_drawdown_pct,
                    'sharpe_ratio': run.sharpe_ratio,
                    'kest_total': _tax.get('kest_total', 0.0),
                    'commission_total': _tax.get('commission_total', 0.0),
                    'total_trades': run.total_trades,
                })

                update_scenario_batch(batch_id, {
                    'current_index': index + 1,
                    'run_ids': run_ids,
                    'results': results,
                })

            update_scenario_batch(batch_id, {
                'status': 'completed',
                'finished_at': datetime.now(timezone.utc).isoformat(),
                'run_ids': run_ids,
                'results': results,
                'current_index': len(current_batch.get('scenario_ids') or []),
            })
            try:
                from services.telegram_notifier import notify_batch_complete
                notify_batch_complete(current_batch.get('name', batch_id), results)
            except Exception:
                pass
        except Exception as e:
            log.exception('Scenario batch fehlgeschlagen: %s', batch_id)
            update_scenario_batch(batch_id, {
                'status': 'failed',
                'finished_at': datetime.now(timezone.utc).isoformat(),
                'error': str(e),
            })
            try:
                from services.telegram_notifier import send_message, esc
                send_message(f'Batch {esc(batch_id)} fehlgeschlagen: {esc(e)}')
            except Exception:
                pass

    thread = threading.Thread(
        target=background_batch_runner,
        name=f"scenario-batch-{batch_id}",
        daemon=True,
    )
    thread.start()
    return True, None
