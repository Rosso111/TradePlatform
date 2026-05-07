"""
Portfolio Repository — zentralisiert alle Portfolio-Abfragen.
Verhindert Duplikation von get_active_portfolio() und portfolio-bezogenen Queries.
"""
from flask import session
from flask_login import current_user
from models import Portfolio


def get_active_portfolio() -> Portfolio | None:
    """Gibt das aktive Portfolio des aktuellen Users zurück.
    Priorisiert das in der Session gesetzte Portfolio, fällt auf das
    erste aktive Portfolio des Users zurück.
    Implements: G-02, P-05, API-27, API-28
    """
    pid = session.get('active_portfolio_id')
    if pid:
        portfolio = Portfolio.query.filter_by(id=pid, user_id=current_user.id).first()
        if portfolio:
            return portfolio
    return (Portfolio.query
            .filter_by(user_id=current_user.id, status='active')
            .order_by(Portfolio.id)
            .first())


def get_portfolio(portfolio_id: int, user_id: int | None = None) -> Portfolio | None:
    """Lädt ein Portfolio per ID, optional auf einen User eingeschränkt."""
    q = Portfolio.query.filter_by(id=portfolio_id)
    if user_id is not None:
        q = q.filter_by(user_id=user_id)
    return q.first()


def get_active_auto_portfolios() -> list[Portfolio]:
    """Gibt alle aktiven Auto-Portfolios zurück (für den Scheduler)."""
    return Portfolio.query.filter_by(status='active', mode='auto').all()


def get_active_portfolios() -> list[Portfolio]:
    """Gibt alle aktiven Portfolios zurück."""
    return Portfolio.query.filter_by(status='active').all()
