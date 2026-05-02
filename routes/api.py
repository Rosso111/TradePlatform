"""
API Blueprint — Legacy-Stub.
Alle Routen wurden in domänenspezifische Blueprints aufgeteilt:
  routes/trading.py     — Account, Positionen, Trades, Equity, Signale
  routes/simulations.py — Historische Simulationen
  routes/scenarios.py   — Szenarien und Batches
  routes/strategies.py  — Strategien und Universen
"""
from flask import Blueprint, jsonify, session
from flask_login import login_required, current_user
from models import Portfolio

api = Blueprint('api', __name__, url_prefix='/api')


def get_active_portfolio() -> Portfolio | None:
    """Implements: G-02, P-05, API-27, API-28"""
    from routes.common import get_active_portfolio as _get
    return _get()


@api.route('/portfolios/<int:portfolio_id>/activate', methods=['POST'])
@login_required
def activate_portfolio(portfolio_id):
    """Implements: G-02, P-05, API-28"""
    portfolio = Portfolio.query.filter_by(
        id=portfolio_id, user_id=current_user.id
    ).first()
    if not portfolio:
        return jsonify({'error': 'Portfolio nicht gefunden'}), 404
    session['active_portfolio_id'] = portfolio_id
    return jsonify({'active_portfolio_id': portfolio_id, 'portfolio': portfolio.to_dict()})
