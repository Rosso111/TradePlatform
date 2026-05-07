"""
Strategy Routes — Strategien, Regeln und Universen
"""
from flask import Blueprint, jsonify, request
from flask_login import login_required, current_user
import logging

from services.strategy_store import (
    list_strategies, upsert_strategy, set_active_strategy, approve_strategy_for_live,
)
from services.universe_store import list_universes
from models import db, Strategy, StrategyRule

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


# ── Strategy Rules (S-06..S-10, API-19..22) ───────────────────────────────────

def _check_strategy_access(strategy: Strategy):
    """Gibt (True, None) oder (False, error-response) zurück."""
    if strategy is None:
        return False, (jsonify({'error': 'Strategie nicht gefunden'}), 404)
    if not strategy.is_system and strategy.user_id != current_user.id and current_user.role != 'admin':
        return False, (jsonify({'error': 'Zugriff verweigert'}), 403)
    return True, None


@strategies_bp.route('/strategies/<int:strategy_id>/rules', methods=['GET'])
@login_required
def list_rules(strategy_id):
    # Implements: API-19
    strategy = Strategy.query.get(strategy_id)
    ok, err = _check_strategy_access(strategy)
    if not ok:
        return err
    return jsonify([r.to_dict() for r in strategy.rules.all()])


@strategies_bp.route('/strategies/<int:strategy_id>/rules', methods=['POST'])
@login_required
def create_rule(strategy_id):
    # Implements: API-20, S-06
    strategy = Strategy.query.get(strategy_id)
    ok, err = _check_strategy_access(strategy)
    if not ok:
        return err

    payload = request.get_json(silent=True) or {}
    level     = payload.get('level', '').strip()
    key       = payload.get('key', '').strip()
    overrides = payload.get('overrides', {})

    if level not in ('market', 'sector', 'stock'):
        return jsonify({'error': 'level muss market, sector oder stock sein'}), 400
    if not key:
        return jsonify({'error': 'key darf nicht leer sein'}), 400
    if not isinstance(overrides, dict):
        return jsonify({'error': 'overrides muss ein Objekt sein'}), 400

    existing = StrategyRule.query.filter_by(
        strategy_id=strategy_id, level=level, key=key
    ).first()
    if existing:
        return jsonify({'error': f'Regel für {level}/{key} existiert bereits'}), 409

    rule = StrategyRule(strategy_id=strategy_id, level=level, key=key, overrides=overrides)
    db.session.add(rule)
    db.session.commit()
    return jsonify(rule.to_dict()), 201


@strategies_bp.route('/strategies/<int:strategy_id>/rules/<int:rule_id>', methods=['PUT'])
@login_required
def update_rule(strategy_id, rule_id):
    # Implements: API-21, S-07
    strategy = Strategy.query.get(strategy_id)
    ok, err = _check_strategy_access(strategy)
    if not ok:
        return err

    rule = StrategyRule.query.filter_by(id=rule_id, strategy_id=strategy_id).first()
    if not rule:
        return jsonify({'error': 'Regel nicht gefunden'}), 404

    payload = request.get_json(silent=True) or {}
    if 'overrides' in payload:
        if not isinstance(payload['overrides'], dict):
            return jsonify({'error': 'overrides muss ein Objekt sein'}), 400
        rule.overrides = payload['overrides']
    if 'key' in payload:
        rule.key = payload['key'].strip()
    if 'level' in payload:
        if payload['level'] not in ('market', 'sector', 'stock'):
            return jsonify({'error': 'level muss market, sector oder stock sein'}), 400
        rule.level = payload['level']

    db.session.commit()
    return jsonify(rule.to_dict())


@strategies_bp.route('/strategies/<int:strategy_id>/rules/<int:rule_id>', methods=['DELETE'])
@login_required
def delete_rule(strategy_id, rule_id):
    # Implements: API-22
    strategy = Strategy.query.get(strategy_id)
    ok, err = _check_strategy_access(strategy)
    if not ok:
        return err

    rule = StrategyRule.query.filter_by(id=rule_id, strategy_id=strategy_id).first()
    if not rule:
        return jsonify({'error': 'Regel nicht gefunden'}), 404

    db.session.delete(rule)
    db.session.commit()
    return jsonify({'success': True})
