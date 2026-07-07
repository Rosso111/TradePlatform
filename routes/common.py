from flask import abort, jsonify, make_response, request

from models import Portfolio
from repositories.portfolio_repo import get_active_portfolio

__all__ = ['get_active_portfolio', 'query_int']


def query_int(name, default, min_value=None, max_value=None):
    """Liest einen Integer-Query-Parameter; ungültige Werte → 400 statt 500 (REED-6)."""
    raw = request.args.get(name)
    if raw in (None, ''):
        value = default
    else:
        try:
            value = int(raw)
        except ValueError:
            abort(make_response(
                jsonify({'error': f"Ungültiger Wert für '{name}': ganze Zahl erwartet"}), 400))
    if min_value is not None:
        value = max(value, min_value)
    if max_value is not None:
        value = min(value, max_value)
    return value
