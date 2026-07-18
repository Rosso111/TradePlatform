"""Preis-Update muss Aktien mit offener Position auch außerhalb des Universums abdecken.

Hintergrund: 16 per IBKR-Sync importierte Positionen liefen ab 24.04.2026 ohne
Kurs-Updates (und damit ohne SL/TP-Überwachung), weil ihre Symbole nicht in
config.STOCK_UNIVERSE stehen.
"""
from models import Position, Stock
from services.data_fetcher import _with_position_stocks

UNIVERSE = [{'symbol': 'AAPL', 'name': 'Apple Inc.', 'sector': 'Technology',
             'region': 'US', 'currency': 'USD'}]


def _add_stock(db, symbol, currency='USD'):
    s = Stock(symbol=symbol, name=symbol, sector='Industrials',
              region='US', currency=currency, active=True)
    db.session.add(s)
    db.session.commit()
    return s


def _add_position(db, portfolio, stock):
    p = Position(portfolio_id=portfolio.id, stock_id=stock.id, shares=10,
                 entry_price=100.0, entry_price_eur=92.0, cost_eur=920.0)
    db.session.add(p)
    db.session.commit()
    return p


def test_position_stock_outside_universe_is_added(app, db, portfolio):
    trex = _add_stock(db, 'TREX')
    _add_position(db, portfolio, trex)

    result = _with_position_stocks(app, UNIVERSE)

    symbols = [s['symbol'] for s in result]
    assert symbols == ['AAPL', 'TREX']
    added = result[1]
    assert added['currency'] == 'USD'
    assert added['name'] == 'TREX'


def test_stock_without_position_is_not_added(app, db):
    _add_stock(db, 'CVBF')

    result = _with_position_stocks(app, UNIVERSE)

    assert [s['symbol'] for s in result] == ['AAPL']


def test_universe_stock_with_position_is_not_duplicated(app, db, portfolio, stock):
    _add_position(db, portfolio, stock)  # stock-Fixture ist AAPL

    result = _with_position_stocks(app, UNIVERSE)

    assert [s['symbol'] for s in result] == ['AAPL']
