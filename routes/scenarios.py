"""
Scenario Routes — Szenarien und Scenario-Batches
"""
from flask import Blueprint, jsonify, request
from flask_login import login_required
from datetime import datetime, timezone
import logging
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
    # Implements: API-27 — Logik liegt in services/scenario_runner (VANCE-M3)
    from flask import current_app
    from services.scenario_runner import start_batch

    ok, error = start_batch(current_app._get_current_object(), batch_id)
    if not ok:
        status = 404 if error == 'Batch nicht gefunden' else 409
        return jsonify({'success': False, 'error': error}), status

    refreshed = get_scenario_batch(batch_id)
    return jsonify({'success': True, 'batch': refreshed})
