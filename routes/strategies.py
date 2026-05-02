"""
Strategy Routes — Strategien und Universen
"""
from flask import Blueprint, jsonify, request
from flask_login import login_required, current_user
import logging

from services.strategy_store import (
    list_strategies, upsert_strategy, set_active_strategy, approve_strategy_for_live,
)
from services.universe_store import list_universes

log = logging.getLogger(__name__)
strategies_bp = Blueprint('strategies', __name__, url_prefix='/api')


@strategies_bp.route('/strategies', methods=['GET'])
@login_required
def get_strategies():
    # Implements: API-15, API-27
    return jsonify(list_strategies())


@strategies_bp.route('/strategies', methods=['POST'])
@login_required
def create_strategy():
    # Implements: S-01, API-16
    payload = request.get_json(silent=True) or {}
    payload.setdefault('user_id', current_user.id)
    try:
        saved = upsert_strategy(payload)
        return jsonify({'success': True, 'strategy': saved}), 201
    except (ValueError, PermissionError) as e:
        return jsonify({'success': False, 'error': str(e)}), 400


@strategies_bp.route('/strategies/active', methods=['POST'])
@login_required
def update_active_strategy():
    # Implements: API-15
    payload = request.get_json(silent=True) or {}
    strategy_id = payload.get('strategy_id')
    if not strategy_id:
        return jsonify({'success': False, 'error': 'strategy_id fehlt'}), 400
    try:
        data = set_active_strategy(strategy_id)
        return jsonify({'success': True, **data})
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 404


@strategies_bp.route('/strategies/<strategy_id>', methods=['PUT'])
@login_required
def update_strategy(strategy_id):
    # Implements: API-17, S-05
    payload = request.get_json(silent=True) or {}
    payload['id'] = strategy_id
    try:
        saved = upsert_strategy(payload)
        return jsonify({'success': True, 'strategy': saved})
    except PermissionError as e:
        return jsonify({'success': False, 'error': str(e)}), 403
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400


@strategies_bp.route('/strategies/<strategy_id>/approve-live', methods=['POST'])
@login_required
def approve_strategy_live(strategy_id):
    # Implements: S-02, API-17
    if current_user.role != 'admin':
        return jsonify({'error': 'Nur Admins können Strategien freigeben'}), 403
    try:
        data = approve_strategy_for_live(strategy_id)
        return jsonify({'success': True, **data})
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400


@strategies_bp.route('/universes', methods=['GET'])
@login_required
def get_universes():
    # Implements: API-27
    return jsonify(list_universes())
