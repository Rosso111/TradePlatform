"""
Portfolio-Management Routes
CRUD + Status-Toggle für Portfolios.
"""
import logging
from flask import Blueprint, jsonify, request
from flask_login import login_required, current_user

from models import db, Portfolio, Account, Position

log = logging.getLogger(__name__)

portfolios_bp = Blueprint('portfolios', __name__, url_prefix='/api/portfolios')

_VALID_TYPES = ('sim', 'ibkr_paper', 'ibkr_live')
_VALID_MODES = ('auto', 'approval')
_IMMUTABLE_FIELDS = ('type', 'currency', 'starting_capital')


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _get_portfolio_or_403(portfolio_id):
    """Gibt (portfolio, None, None) oder (None, response, status_code) zurück."""
    portfolio = Portfolio.query.get_or_404(portfolio_id)
    if portfolio.user_id != current_user.id and current_user.role != 'admin':
        return None, jsonify({'error': 'Keine Berechtigung'}), 403
    return portfolio, None, None


# ---------------------------------------------------------------------------
# Route 1 — GET /api/portfolios
# Implements: API-09, P-01, G-01
# ---------------------------------------------------------------------------

@portfolios_bp.route('', methods=['GET'])
@login_required
def list_portfolios():
    if current_user.role == 'admin':
        portfolios = Portfolio.query.order_by(Portfolio.id).all()
    else:
        portfolios = (
            Portfolio.query
            .filter_by(user_id=current_user.id)
            .order_by(Portfolio.id)
            .all()
        )
    return jsonify([p.to_dict() for p in portfolios])


# ---------------------------------------------------------------------------
# Route 2 — POST /api/portfolios
# Implements: API-10, P-01, G-01
# ---------------------------------------------------------------------------

@portfolios_bp.route('', methods=['POST'])
@login_required
def create_portfolio():
    data = request.get_json(silent=True) or {}

    name = data.get('name', '').strip()
    if not name:
        return jsonify({'error': 'Name ist erforderlich'}), 400

    ptype = data.get('type', 'sim')
    if ptype not in _VALID_TYPES:
        return jsonify({'error': f'Ungültiger type ({"|".join(_VALID_TYPES)})'}), 400

    mode = data.get('mode', 'auto')
    if mode not in _VALID_MODES:
        return jsonify({'error': f'Ungültiger mode ({"|".join(_VALID_MODES)})'}), 400

    currency = data.get('currency', 'EUR')
    starting_capital = data.get('starting_capital', 10000.0)
    strategy_id = data.get('strategy_id') or None

    portfolio = Portfolio(
        user_id=current_user.id,
        name=name,
        type=ptype,
        mode=mode,
        currency=currency,
        starting_capital=starting_capital,
        strategy_id=strategy_id,
    )
    db.session.add(portfolio)
    db.session.flush()  # portfolio.id verfügbar machen

    account = Account(
        portfolio_id=portfolio.id,
        cash_eur=portfolio.starting_capital,
        equity_eur=portfolio.starting_capital,
    )
    db.session.add(account)
    db.session.commit()

    log.info(
        "User '%s' hat Portfolio '%s' (id=%s) angelegt.",
        current_user.username, portfolio.name, portfolio.id,
    )
    return jsonify(portfolio.to_dict()), 201


# ---------------------------------------------------------------------------
# Route 3 — GET /api/portfolios/<id>
# Implements: API-11, P-05
# ---------------------------------------------------------------------------

@portfolios_bp.route('/<int:portfolio_id>', methods=['GET'])
@login_required
def get_portfolio(portfolio_id):
    portfolio, err_response, err_code = _get_portfolio_or_403(portfolio_id)
    if err_response:
        return err_response, err_code

    result = portfolio.to_dict()
    if portfolio.account:
        result['account'] = portfolio.account.to_dict()
    return jsonify(result)


# ---------------------------------------------------------------------------
# Route 4 — PUT /api/portfolios/<id>
# Implements: API-12
# ---------------------------------------------------------------------------

@portfolios_bp.route('/<int:portfolio_id>', methods=['PUT'])
@login_required
def update_portfolio(portfolio_id):
    portfolio, err_response, err_code = _get_portfolio_or_403(portfolio_id)
    if err_response:
        return err_response, err_code

    data = request.get_json(silent=True) or {}

    # Unveränderliche Felder abweisen
    for field in _IMMUTABLE_FIELDS:
        if field in data:
            return jsonify({'error': f'Feld "{field}" kann nach Anlage nicht geändert werden'}), 400

    # name
    new_name = data.get('name', '').strip()
    if new_name and new_name != portfolio.name:
        duplicate = (
            Portfolio.query
            .filter_by(user_id=portfolio.user_id, name=new_name)
            .first()
        )
        if duplicate:
            return jsonify({'error': 'Portfolio-Name bereits vergeben'}), 409
        portfolio.name = new_name

    # mode
    if 'mode' in data:
        new_mode = data['mode']
        if new_mode not in _VALID_MODES:
            return jsonify({'error': f'Ungültiger mode ({"|".join(_VALID_MODES)})'}), 400
        portfolio.mode = new_mode

    # strategy_id
    if 'strategy_id' in data:
        portfolio.strategy_id = data['strategy_id'] or None

    db.session.commit()
    return jsonify(portfolio.to_dict())


# ---------------------------------------------------------------------------
# Route 5 — PATCH /api/portfolios/<id>/status
# Implements: API-13, P-02, P-03
# ---------------------------------------------------------------------------

@portfolios_bp.route('/<int:portfolio_id>/status', methods=['PATCH'])
@login_required
def toggle_portfolio_status(portfolio_id):
    portfolio, err_response, err_code = _get_portfolio_or_403(portfolio_id)
    if err_response:
        return err_response, err_code

    # Guard: letztes aktives Portfolio darf nicht deaktiviert werden
    if portfolio.status == 'active':
        active_count = (
            Portfolio.query
            .filter_by(user_id=portfolio.user_id, status='active')
            .count()
        )
        if active_count <= 1:
            return jsonify({'error': 'Mindestens ein Portfolio muss aktiv bleiben'}), 400

    portfolio.status = 'inactive' if portfolio.status == 'active' else 'active'
    db.session.commit()
    log.info(
        "User '%s' hat Portfolio '%s' (id=%s) auf '%s' gesetzt.",
        current_user.username, portfolio.name, portfolio.id, portfolio.status,
    )
    return jsonify(portfolio.to_dict())


# ---------------------------------------------------------------------------
# Route 6 — DELETE /api/portfolios/<id>
# Implements: API-14, P-04
# ---------------------------------------------------------------------------

@portfolios_bp.route('/<int:portfolio_id>', methods=['DELETE'])
@login_required
def delete_portfolio(portfolio_id):
    portfolio, err_response, err_code = _get_portfolio_or_403(portfolio_id)
    if err_response:
        return err_response, err_code

    # Guard: letztes Portfolio darf nicht gelöscht werden
    total_count = Portfolio.query.filter_by(user_id=portfolio.user_id).count()
    if total_count <= 1:
        return jsonify({'error': 'Das letzte Portfolio eines Users kann nicht gelöscht werden'}), 400

    # Guard: keine offenen Positionen
    open_position = Position.query.filter_by(portfolio_id=portfolio.id).first()
    if open_position:
        return jsonify({'error': 'Portfolio hat noch offene Positionen und kann nicht gelöscht werden'}), 400

    # Account explizit entfernen (Cascade sollte greifen, aber sicher ist sicher)
    if portfolio.account:
        db.session.delete(portfolio.account)

    db.session.delete(portfolio)
    db.session.commit()
    log.info(
        "User '%s' hat Portfolio '%s' (id=%s) gelöscht.",
        current_user.username, portfolio.name, portfolio.id,
    )
    return jsonify({'message': f'Portfolio {portfolio_id} gelöscht'}), 200
