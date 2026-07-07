"""
Trading Routes — Account, Positionen, Trades, Equity, Signale, Watchlist
"""
from flask import Blueprint, jsonify, request
from flask_login import login_required, current_user
from datetime import date, timedelta
import logging

from app import limiter

from sqlalchemy import func, and_
from models import (
    db, Account, Position, Trade, Stock, Price, Signal, EquityHistory, AlgoParams,
)
from repositories.portfolio_repo import get_active_portfolio
from routes.common import query_int
from repositories.trading_repo import (
    get_account, get_positions, count_positions, get_trades,
    get_equity_history, get_latest_signal, get_today_signals,
)

log = logging.getLogger(__name__)
trading_bp = Blueprint('trading', __name__, url_prefix='/api')


# ─── Account ─────────────────────────────────────────────────────────────────

@trading_bp.route('/account')
@login_required
def account_view():
    # Implements: P-05, API-27
    portfolio = get_active_portfolio()
    if portfolio is None:
        return jsonify({'error': 'Kein aktives Portfolio gefunden'}), 404

    account = get_account(portfolio.id)
    if not account:
        return jsonify({'error': 'Kein Konto gefunden'}), 404

    positions = get_positions(portfolio.id)
    positions_value = sum(
        (p.current_price_eur or p.entry_price_eur) * p.shares
        for p in positions
    )
    total_pnl = sum(p.unrealized_pnl_eur() for p in positions)
    total_cost_basis = sum(p.shares * p.entry_price_eur for p in positions)
    total_pnl_pct = (total_pnl / total_cost_basis * 100) if total_cost_basis > 0 else 0

    data = account.to_dict()
    data.update({
        'positions_value': round(positions_value, 2),
        'open_positions': len(positions),
        'unrealized_pnl_eur': round(total_pnl, 2),
        'unrealized_pnl_pct': round(total_pnl_pct, 2),
        'total_return_eur': round(account.equity_eur - (account.portfolio.starting_capital if account.portfolio else 10000.0), 2),
        'total_return_pct': round((account.equity_eur - (account.portfolio.starting_capital if account.portfolio else 10000.0)) / (account.portfolio.starting_capital if account.portfolio else 10000.0) * 100, 2),
    })
    return jsonify(data)


# ─── Positionen ──────────────────────────────────────────────────────────────

@trading_bp.route('/positions')
@login_required
def positions_view():
    # Implements: P-05, API-27
    portfolio = get_active_portfolio()
    if portfolio is None:
        return jsonify({'error': 'Kein aktives Portfolio gefunden'}), 404

    positions = get_positions(portfolio.id)
    return jsonify([p.to_dict() for p in positions])


@trading_bp.route('/portfolio/summary')
@login_required
def portfolio_summary():
    # Implements: P-05, API-27
    portfolio = get_active_portfolio()
    if portfolio is None:
        return jsonify({'error': 'Kein aktives Portfolio gefunden'}), 404

    positions = get_positions(portfolio.id)
    by_sector = {}
    by_region = {}

    for p in positions:
        s = p.stock.sector
        r = p.stock.region
        value = (p.current_price_eur or p.entry_price_eur) * p.shares
        by_sector[s] = by_sector.get(s, 0) + value
        by_region[r] = by_region.get(r, 0) + value

    return jsonify({
        'by_sector': by_sector,
        'by_region': by_region,
        'positions': [p.to_dict() for p in positions],
    })


# ─── Trades ──────────────────────────────────────────────────────────────────

@trading_bp.route('/trades')
@login_required
def trades_view():
    # Implements: P-05, API-27
    portfolio = get_active_portfolio()
    if portfolio is None:
        return jsonify({'error': 'Kein aktives Portfolio gefunden'}), 404

    limit = query_int('limit', 50, min_value=1, max_value=1000)
    trades = get_trades(portfolio.id, limit=limit)
    return jsonify([t.to_dict() for t in trades])


@trading_bp.route('/trades/stats')
@login_required
def trade_stats():
    # Implements: P-05, API-27
    portfolio = get_active_portfolio()
    if portfolio is None:
        return jsonify({'error': 'Kein aktives Portfolio gefunden'}), 404

    trades = get_trades(portfolio.id, limit=10000, action='SELL')
    if not trades:
        return jsonify({'total': 0, 'wins': 0, 'losses': 0,
                        'win_rate': 0, 'avg_pnl': 0, 'best': 0, 'worst': 0})

    wins = [t for t in trades if t.pnl_eur > 0]
    losses = [t for t in trades if t.pnl_eur <= 0]
    pnl_list = [t.pnl_eur for t in trades]

    return jsonify({
        'total': len(trades),
        'wins': len(wins),
        'losses': len(losses),
        'win_rate': round(len(wins) / len(trades) * 100, 1),
        'avg_pnl': round(sum(pnl_list) / len(pnl_list), 2),
        'total_pnl': round(sum(pnl_list), 2),
        'best': round(max(pnl_list), 2),
        'worst': round(min(pnl_list), 2),
    })


# ─── Kursdaten ───────────────────────────────────────────────────────────────

@trading_bp.route('/prices/<symbol>')
@login_required
def get_prices(symbol):
    # Implements: API-27
    days = query_int('days', 90, min_value=1)
    stock = Stock.query.filter_by(symbol=symbol).first()
    if not stock:
        return jsonify({'error': f'Symbol {symbol} nicht gefunden'}), 404

    since = date.today() - timedelta(days=days)
    prices = (Price.query
              .filter(Price.stock_id == stock.id, Price.date >= since)
              .order_by(Price.date.asc())
              .all())

    return jsonify({
        'symbol': symbol,
        'name': stock.name,
        'currency': stock.currency,
        'sector': stock.sector,
        'region': stock.region,
        'prices': [p.to_dict() for p in prices],
    })


# ─── Watchlist ───────────────────────────────────────────────────────────────

@trading_bp.route('/watchlist')
@login_required
def get_watchlist():
    # Implements: API-27
    portfolio = get_active_portfolio()
    if portfolio is None:
        return jsonify({'error': 'Kein aktives Portfolio gefunden'}), 404

    stocks = Stock.query.filter_by(active=True).all()
    if not stocks:
        return jsonify([])

    stock_ids = [s.id for s in stocks]

    # Latest date per stock
    max_price_date = (
        db.session.query(Price.stock_id, func.max(Price.date).label('max_date'))
        .filter(Price.stock_id.in_(stock_ids))
        .group_by(Price.stock_id)
        .subquery()
    )
    latest_prices = {
        p.stock_id: p
        for p in db.session.query(Price).join(
            max_price_date,
            and_(Price.stock_id == max_price_date.c.stock_id,
                 Price.date == max_price_date.c.max_date)
        ).all()
    }

    # Second-to-last date per stock (for change_pct)
    prev_price_date = (
        db.session.query(Price.stock_id, func.max(Price.date).label('prev_date'))
        .join(max_price_date, and_(
            Price.stock_id == max_price_date.c.stock_id,
            Price.date < max_price_date.c.max_date
        ))
        .group_by(Price.stock_id)
        .subquery()
    )
    prev_prices = {
        p.stock_id: p
        for p in db.session.query(Price).join(
            prev_price_date,
            and_(Price.stock_id == prev_price_date.c.stock_id,
                 Price.date == prev_price_date.c.prev_date)
        ).all()
    }

    # Latest signal per stock
    max_signal_date = (
        db.session.query(Signal.stock_id, func.max(Signal.date).label('max_date'))
        .filter(Signal.stock_id.in_(stock_ids))
        .group_by(Signal.stock_id)
        .subquery()
    )
    latest_signals = {
        s.stock_id: s
        for s in db.session.query(Signal).join(
            max_signal_date,
            and_(Signal.stock_id == max_signal_date.c.stock_id,
                 Signal.date == max_signal_date.c.max_date)
        ).all()
    }

    # Open positions for this portfolio
    portfolio_stock_ids = {
        p.stock_id
        for p in Position.query.filter_by(portfolio_id=portfolio.id).all()
    }

    result = []
    for stock in stocks:
        lp = latest_prices.get(stock.id)
        if not lp:
            continue
        pp = prev_prices.get(stock.id)
        sig = latest_signals.get(stock.id)

        change_pct = 0.0
        if pp and pp.close > 0:
            change_pct = (lp.close - pp.close) / pp.close * 100

        result.append({
            'symbol': stock.symbol,
            'name': stock.name,
            'sector': stock.sector,
            'region': stock.region,
            'currency': stock.currency,
            'price': round(lp.close, 4),
            'price_eur': round(lp.close_eur or lp.close, 4),
            'change_pct': round(change_pct, 2),
            'score': round(sig.score, 1) if sig else None,
            'action': sig.action if sig else 'HOLD',
            'in_portfolio': stock.id in portfolio_stock_ids,
        })

    result.sort(key=lambda x: x.get('score') or 0, reverse=True)
    return jsonify(result)


# ─── Equity-Kurve ────────────────────────────────────────────────────────────

@trading_bp.route('/equity')
@login_required
def get_equity():
    # Implements: P-05, API-27
    portfolio = get_active_portfolio()
    if portfolio is None:
        return jsonify({'error': 'Kein aktives Portfolio gefunden'}), 404

    days = query_int('days', 30, min_value=1)
    since = date.today() - timedelta(days=days)
    history = get_equity_history(portfolio.id, since=since)
    return jsonify([h.to_dict() for h in history])


# ─── Signale ─────────────────────────────────────────────────────────────────

@trading_bp.route('/signals')
@login_required
def get_signals():
    # Implements: P-05, API-27
    portfolio = get_active_portfolio()
    if portfolio is None:
        return jsonify({'error': 'Kein aktives Portfolio gefunden'}), 404

    signals = get_today_signals(portfolio.id, date.today())
    return jsonify([s.to_dict() for s in signals])


# ─── Algo-Parameter ──────────────────────────────────────────────────────────

@trading_bp.route('/algo/params')
@login_required
def get_algo_params():
    # Implements: API-27
    params = (AlgoParams.query
              .join(Stock)
              .order_by(AlgoParams.sharpe_ratio.desc())
              .all())
    result = []
    for p in params:
        result.append({
            'symbol': p.stock.symbol,
            'name': p.stock.name,
            'sharpe_ratio': round(p.sharpe_ratio, 3),
            'backtest_return': round(p.backtest_return, 2),
            'rsi_period': p.rsi_period,
            'ema_fast': p.ema_fast,
            'ema_slow': p.ema_slow,
            'optimized_at': p.optimized_at.isoformat() if p.optimized_at else None,
        })
    return jsonify(result)


# ─── Manueller Trigger ───────────────────────────────────────────────────────

@trading_bp.route('/trading/run', methods=['POST'])
@login_required
@limiter.limit("3 per minute; 10 per hour")
def trigger_trading_cycle():
    # Implements: G-02, API-27
    portfolio = get_active_portfolio()
    if portfolio is None:
        return jsonify({'error': 'Kein aktives Portfolio gefunden'}), 404
    if portfolio.mode != 'auto':
        return jsonify({'error': 'Nur Auto-Portfolios können manuell getriggert werden'}), 400

    from flask import current_app
    from services.trading_engine import run_trading_cycle
    try:
        actions = run_trading_cycle(current_app._get_current_object(), portfolio_id=portfolio.id)
        return jsonify({'success': True, 'actions': actions})
    except Exception as e:
        log.error(f"Manueller Trigger: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@trading_bp.route('/trading/optimize', methods=['POST'])
@login_required
@limiter.limit("1 per hour")
def trigger_optimization():
    # Implements: API-27
    from flask import current_app
    from services.algorithm import run_optimization_for_all
    try:
        run_optimization_for_all(current_app._get_current_object())
        return jsonify({'success': True, 'message': 'Optimierung abgeschlossen'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ─── Status ──────────────────────────────────────────────────────────────────

@trading_bp.route('/status')
@login_required
def get_status():
    # Implements: API-27
    portfolio = get_active_portfolio()
    if portfolio is None:
        return jsonify({'error': 'Kein aktives Portfolio gefunden'}), 404

    account = get_account(portfolio.id)
    positions_count = count_positions(portfolio.id)
    trades_count = Trade.query.filter_by(portfolio_id=portfolio.id).count()
    stocks_count = Stock.query.filter_by(active=True).count()
    latest_signal = get_latest_signal(portfolio.id)

    return jsonify({
        'ready': True,
        'stocks_loaded': stocks_count,
        'open_positions': positions_count,
        'total_trades': trades_count,
        'equity_eur': round(account.equity_eur, 2) if account else 0,
        'last_signal': latest_signal.created_at.isoformat() if latest_signal else None,
    })
