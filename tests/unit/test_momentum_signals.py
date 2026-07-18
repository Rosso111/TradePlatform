"""Dual-Momentum-Live-Pfad: Signalgenerator, Fixed-Fraction-Sizing, Min-Hold-Gate."""
from datetime import date, datetime, timedelta, timezone

from models import Position, Price, Stock, Strategy
from services.momentum_signals import generate_momentum_signals
from services.trading_engine import execute_buy, update_positions

FX = {'USD': 1.0, 'EUR': 1.0}


def _mk_strategy(db, **extra_params):
    params = {'momentum_lookback_days': 5, 'top_n_signals': 2,
              'absolute_momentum_threshold': 0.0, 'buy_threshold': 0,
              'sell_threshold': -1, 'max_positions': 4,
              'max_position_size': 0.4, 'min_position_size': 0.25,
              'max_positions_per_sector': 99, 'position_sizing': 'fixed_fraction',
              'trailing_stop_pct': 0.18, 'signal_universe': 'all', **extra_params}
    s = Strategy(slug='dm_test', name='DM Test', mode='dual_momentum', params=params)
    db.session.add(s)
    db.session.commit()
    return s


def _mk_stock_with_prices(db, symbol, closes):
    s = Stock(symbol=symbol, name=symbol, sector='Tech', region='US',
              currency='USD', active=True)
    db.session.add(s)
    db.session.flush()
    start = date.today() - timedelta(days=len(closes))
    for i, close in enumerate(closes):
        db.session.add(Price(stock_id=s.id, date=start + timedelta(days=i),
                             open=close, high=close * 1.01, low=close * 0.99,
                             close=close, volume=1000, close_eur=close))
    db.session.commit()
    return s


def test_top_n_ranking_and_sell_signal(app, db, portfolio):
    strategy = _mk_strategy(db)
    portfolio.strategy_id = strategy.id
    up_strong = _mk_stock_with_prices(db, 'UPS', [100, 110, 120, 130, 140, 150])  # +50%
    up_mild = _mk_stock_with_prices(db, 'UPM', [100, 102, 104, 106, 108, 110])    # +10%
    up_weak = _mk_stock_with_prices(db, 'UPW', [100, 101, 101, 102, 102, 103])    # +3%
    down = _mk_stock_with_prices(db, 'DWN', [100, 95, 90, 85, 80, 75])            # -25%
    _mk_stock_with_prices(db, 'SHORT', [100, 101])  # zu wenig Historie
    db.session.commit()

    signals = generate_momentum_signals(portfolio)
    by_symbol = {s['symbol']: s for s in signals}

    assert by_symbol['UPS']['action'] == 'BUY' and by_symbol['UPS']['dm_rank'] == 1
    assert by_symbol['UPM']['action'] == 'BUY' and by_symbol['UPM']['dm_rank'] == 2
    assert by_symbol['UPW']['action'] == 'HOLD'   # positiv, aber außerhalb Top-2
    assert by_symbol['DWN']['action'] == 'SELL'
    assert 'SHORT' not in by_symbol
    # Sortierung: stärkstes Momentum zuerst
    assert signals[0]['symbol'] == 'UPS'


def test_fixed_fraction_sizing_ignores_20k_cap(app, db, portfolio):
    strategy = _mk_strategy(db)
    portfolio.strategy_id = strategy.id
    portfolio.starting_capital = 1_000_000.0
    account = portfolio.accounts[0] if hasattr(portfolio, 'accounts') else None
    from models import Account
    account = Account.query.filter_by(portfolio_id=portfolio.id).first()
    account.cash_eur = 1_000_000.0
    account.equity_eur = 1_000_000.0
    stock = _mk_stock_with_prices(db, 'BIGPOS', [100] * 6)
    db.session.commit()

    signal = {'stock_id': stock.id, 'symbol': 'BIGPOS', 'sector': 'Tech',
              'currency': 'USD', 'score': 90, 'current_price': 100.0,
              'current_price_eur': 100.0, 'atr': None, 'reason': 'Test'}
    ok, msg = execute_buy(signal, FX, portfolio)
    assert ok, msg
    pos = Position.query.filter_by(portfolio_id=portfolio.id, stock_id=stock.id).first()
    # 40% von 1M Cash ≈ 400k — weit über dem 20k-Cap des Risiko-Sizings
    assert pos.shares * pos.entry_price_eur > 350_000


def test_min_hold_blocks_stop_loss(app, db, portfolio):
    strategy = _mk_strategy(db, min_hold_days=120)
    portfolio.strategy_id = strategy.id
    stock = _mk_stock_with_prices(db, 'HOLDME', [100, 100, 100, 100, 100, 50])
    pos = Position(portfolio_id=portfolio.id, stock_id=stock.id, shares=10,
                   entry_price=100.0, entry_price_eur=100.0, cost_eur=1000.0,
                   stop_loss=80.0, trailing_stop=80.0, highest_price=100.0,
                   opened_at=datetime.now(timezone.utc) - timedelta(days=10))
    db.session.add(pos)
    db.session.commit()

    actions, sold = update_positions(FX, portfolio.id)
    assert sold == set()          # Kurs 50 < Stop 80, aber Min-Hold aktiv
    assert Position.query.get(pos.id) is not None

    # Nach Ablauf der Mindesthaltedauer greift der Stop
    pos = Position.query.get(pos.id)
    pos.opened_at = datetime.now(timezone.utc) - timedelta(days=121)
    db.session.commit()
    actions, sold = update_positions(FX, portfolio.id)
    assert sold == {stock.id}
