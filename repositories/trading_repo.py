"""
Trading Repository — zentralisiert Account-, Positions- und Trade-Abfragen.
Alle DB-Zugriffe auf handelsbezogene Tabellen laufen durch diesen Layer.
"""
from datetime import date as date_type
from models import Account, Position, Trade, Signal, EquityHistory, Stock


def get_account(portfolio_id: int) -> Account | None:
    return Account.query.filter_by(portfolio_id=portfolio_id).first()


def get_positions(portfolio_id: int) -> list[Position]:
    return Position.query.filter_by(portfolio_id=portfolio_id).all()


def count_positions(portfolio_id: int) -> int:
    return Position.query.filter_by(portfolio_id=portfolio_id).count()


def count_sector_positions(portfolio_id: int, sector: str) -> int:
    return (Position.query
            .join(Stock)
            .filter(Stock.sector == sector, Position.portfolio_id == portfolio_id)
            .count())


def has_position(portfolio_id: int, stock_id: int) -> bool:
    return Position.query.filter_by(
        portfolio_id=portfolio_id, stock_id=stock_id
    ).first() is not None


def get_trades(portfolio_id: int, limit: int = 50, action: str | None = None) -> list[Trade]:
    q = Trade.query.filter_by(portfolio_id=portfolio_id)
    if action:
        q = q.filter_by(action=action)
    return q.order_by(Trade.executed_at.desc()).limit(limit).all()


def get_equity_history(portfolio_id: int, since: date_type | None = None) -> list[EquityHistory]:
    q = EquityHistory.query.filter_by(portfolio_id=portfolio_id)
    if since:
        q = q.filter(EquityHistory.date >= since)
    return q.order_by(EquityHistory.date.asc()).all()


def get_latest_signal(portfolio_id: int) -> Signal | None:
    return (Signal.query
            .filter_by(portfolio_id=portfolio_id)
            .order_by(Signal.created_at.desc())
            .first())


def get_today_signals(portfolio_id: int, today: date_type) -> list[Signal]:
    return (Signal.query
            .filter_by(portfolio_id=portfolio_id, date=today)
            .order_by(Signal.score.desc())
            .all())
