"""
Scenario Routes — Szenarien und Scenario-Batches
"""
from flask import Blueprint, jsonify, request
from flask_login import login_required
from datetime import datetime, timezone
import logging
import threading
import uuid

from models import db
from services.scenario_store import (
    list_scenarios,
    upsert_scenario,
    get_scenario,
    delete_scenario,
    create_scenario_batch,
    update_scenario_batch,
    get_scenario_batch,
    delete_scenario_batch,
)

log = logging.getLogger(__name__)
scenarios_bp = Blueprint('scenarios', __name__, url_prefix='/api')


@scenarios_bp.route('/scenarios', methods=['GET'])
@login_required
def get_scenarios():
    # Implements: API-27
    return jsonify(list_scenarios())


@scenarios_bp.route('/scenarios/<scenario_id>', methods=['PUT'])
@login_required
def update_scenario(scenario_id):
    # Implements: API-27
    payload = request.get_json(silent=True) or {}
    payload['id'] = scenario_id
    try:
        saved = upsert_scenario(payload)
        return jsonify({'success': True, 'scenario': saved})
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400


@scenarios_bp.route('/scenarios/<scenario_id>', methods=['DELETE'])
@login_required
def remove_scenario(scenario_id):
    # Implements: API-27
    try:
        delete_scenario(scenario_id)
        return jsonify({'success': True, 'deleted_scenario_id': scenario_id})
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 404


@scenarios_bp.route('/scenario-batches', methods=['POST'])
@login_required
def create_batch():
    # Implements: API-27
    payload = request.get_json(silent=True) or {}
    scenario_ids = payload.get('scenario_ids') or []
    if not scenario_ids:
        return jsonify({'success': False, 'error': 'scenario_ids fehlt'}), 400

    scenarios = []
    for scenario_id in scenario_ids:
        scenario = get_scenario(scenario_id)
        if not scenario:
            return jsonify({'success': False, 'error': f'Szenario {scenario_id} nicht gefunden'}), 404
        scenarios.append(scenario)

    batch_id = payload.get('id') or f"batch_{uuid.uuid4().hex[:10]}"
    batch = {
        'id': batch_id,
        'name': payload.get('name') or f'Scenario Batch {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}',
        'status': 'queued',
        'created_at': datetime.now(timezone.utc).isoformat(),
        'started_at': None,
        'finished_at': None,
        'current_index': 0,
        'scenario_ids': scenario_ids,
        'run_ids': [],
        'results': [],
        'error': None,
    }

    try:
        saved = create_scenario_batch(batch)
        return jsonify({'success': True, 'batch': saved}), 201
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400


@scenarios_bp.route('/scenario-batches/<batch_id>', methods=['GET'])
@login_required
def get_batch(batch_id):
    # Implements: API-27
    batch = get_scenario_batch(batch_id)
    if not batch:
        return jsonify({'success': False, 'error': 'Batch nicht gefunden'}), 404
    return jsonify({'success': True, 'batch': batch})


@scenarios_bp.route('/scenario-batches/<batch_id>', methods=['DELETE'])
@login_required
def delete_batch(batch_id):
    # Implements: API-27
    try:
        delete_scenario_batch(batch_id)
        return jsonify({'success': True, 'deleted_batch_id': batch_id})
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 404


@scenarios_bp.route('/scenario-batches/<batch_id>/run', methods=['POST'])
@login_required
def run_batch(batch_id):
    # Implements: API-27
    from flask import current_app
    from services.replay_engine import create_simulation_run, run_historical_replay

    batch = get_scenario_batch(batch_id)
    if not batch:
        return jsonify({'success': False, 'error': 'Batch nicht gefunden'}), 404

    if str(batch.get('status')).lower() == 'running':
        return jsonify({'success': False, 'error': 'Batch läuft bereits'}), 409

    app_obj = current_app._get_current_object()

    def background_batch_runner():
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

                with app_obj.app_context():
                    run = create_simulation_run(payload)
                    run_historical_replay(app_obj, run.id)
                    db.session.refresh(run)

                import json as _json, re as _re
                _tax = {}
                if run.notes:
                    _m = _re.search(r'tax_summary=(\{.*\})', run.notes)
                    if _m:
                        try:
                            _tax = _json.loads(_m.group(1))
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
                from services.telegram_notifier import send_message
                send_message(f'Batch {batch_id} fehlgeschlagen: {e}')
            except Exception:
                pass

    thread = threading.Thread(
        target=background_batch_runner,
        name=f"scenario-batch-{batch_id}",
        daemon=True,
    )
    thread.start()

    refreshed = get_scenario_batch(batch_id)
    return jsonify({'success': True, 'batch': refreshed})
