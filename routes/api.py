"""
REST API Routes
Liefert alle Daten für das Frontend.
"""
from flask import Blueprint, jsonify, request
from datetime import date, timedelta
import logging
import threading

from models import (
    db, Account, Position, Trade, Stock, Price, Signal, EquityHistory, AlgoParams,
    SimulationRun, SimulationPosition, SimulationTrade, DecisionLog, SimulationDailySnapshot,
)
from services.replay_engine import _calculate_benchmark_return_until_date
from services.strategy_store import list_strategies, get_strategy, upsert_strategy, set_active_strategy, approve_strategy_for_live

log = logging.getLogger(__name__)
api = Blueprint('api', __name__, url_prefix='/api')


# ─── Account ─────────────────────────────────────────────────────────────────

@api.route('/account')
def get_account():
    account = Account.query.first()
    if not account:
        return jsonify({'error': 'Kein Konto gefunden'}), 404

    positions = Position.query.all()
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
        'total_return_eur': round(account.equity_eur - 10000.0, 2),
        'total_return_pct': round((account.equity_eur - 10000.0) / 10000.0 * 100, 2),
    })
    return jsonify(data)


# ─── Portfolio / Positionen ──────────────────────────────────────────────────

@api.route('/positions')
def get_positions():
    positions = Position.query.all()
    return jsonify([p.to_dict() for p in positions])


@api.route('/portfolio/summary')
def portfolio_summary():
    positions = Position.query.all()
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

@api.route('/trades')
def get_trades():
    limit = int(request.args.get('limit', 50))
    trades = (Trade.query
              .order_by(Trade.executed_at.desc())
              .limit(limit).all())
    return jsonify([t.to_dict() for t in trades])


@api.route('/trades/stats')
def trade_stats():
    trades = Trade.query.filter_by(action='SELL').all()
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

@api.route('/prices/<symbol>')
def get_prices(symbol):
    days = int(request.args.get('days', 90))
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


@api.route('/watchlist')
def get_watchlist():
    """Alle Aktien mit aktuellem Kurs und Signal-Score"""
    stocks = Stock.query.filter_by(active=True).all()
    result = []

    for stock in stocks:
        latest_price = (Price.query
                        .filter_by(stock_id=stock.id)
                        .order_by(Price.date.desc())
                        .first())
        latest_signal = (Signal.query
                         .filter_by(stock_id=stock.id)
                         .order_by(Signal.date.desc())
                         .first())

        if not latest_price:
            continue

        prev_price = (Price.query
                      .filter_by(stock_id=stock.id)
                      .order_by(Price.date.desc())
                      .offset(1).first())

        change_pct = 0.0
        if prev_price and prev_price.close > 0:
            change_pct = (latest_price.close - prev_price.close) / prev_price.close * 100

        result.append({
            'symbol': stock.symbol,
            'name': stock.name,
            'sector': stock.sector,
            'region': stock.region,
            'currency': stock.currency,
            'price': round(latest_price.close, 4),
            'price_eur': round(latest_price.close_eur or latest_price.close, 4),
            'change_pct': round(change_pct, 2),
            'score': round(latest_signal.score, 1) if latest_signal else None,
            'action': latest_signal.action if latest_signal else 'HOLD',
            'in_portfolio': (Position.query.filter_by(stock_id=stock.id).first() is not None),
        })

    result.sort(key=lambda x: x.get('score') or 0, reverse=True)
    return jsonify(result)


# ─── Equity-Kurve ────────────────────────────────────────────────────────────

@api.route('/equity')
def get_equity():
    days = int(request.args.get('days', 30))
    since = date.today() - timedelta(days=days)
    history = (EquityHistory.query
               .filter(EquityHistory.date >= since)
               .order_by(EquityHistory.date.asc())
               .all())
    return jsonify([h.to_dict() for h in history])


# ─── Signale ─────────────────────────────────────────────────────────────────

@api.route('/signals')
def get_signals():
    today = date.today()
    signals = (Signal.query
               .filter_by(date=today)
               .order_by(Signal.score.desc())
               .all())
    return jsonify([s.to_dict() for s in signals])


# ─── Algo-Parameter ──────────────────────────────────────────────────────────

@api.route('/algo/params')
def get_algo_params():
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


# ─── Strategien ──────────────────────────────────────────────────────────────

@api.route('/strategies', methods=['GET'])
def get_strategies():
    return jsonify(list_strategies())


@api.route('/strategies/active', methods=['POST'])
def update_active_strategy():
    payload = request.get_json(silent=True) or {}
    strategy_id = payload.get('strategy_id')
    if not strategy_id:
        return jsonify({'success': False, 'error': 'strategy_id fehlt'}), 400
    try:
        data = set_active_strategy(strategy_id)
        return jsonify({'success': True, **data})
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 404


@api.route('/strategies/<strategy_id>', methods=['PUT'])
def update_strategy(strategy_id):
    payload = request.get_json(silent=True) or {}
    payload['id'] = strategy_id
    try:
        saved = upsert_strategy(payload)
        return jsonify({'success': True, 'strategy': saved})
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400


@api.route('/strategies/<strategy_id>/approve-live', methods=['POST'])
def approve_strategy_live(strategy_id):
    try:
        data = approve_strategy_for_live(strategy_id)
        return jsonify({'success': True, **data})
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400


# ─── Manueller Trigger ───────────────────────────────────────────────────────

@api.route('/trading/run', methods=['POST'])
def trigger_trading_cycle():
    """Manueller Auslöser für einen Handelszyklus (für Tests)"""
    from flask import current_app
    from services.trading_engine import run_trading_cycle
    try:
        actions = run_trading_cycle(current_app._get_current_object())
        return jsonify({'success': True, 'actions': actions})
    except Exception as e:
        log.error(f"Manueller Trigger: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@api.route('/trading/optimize', methods=['POST'])
def trigger_optimization():
    """Manueller Auslöser für Backtesting-Optimierung"""
    from flask import current_app
    from services.algorithm import run_optimization_for_all
    try:
        run_optimization_for_all(current_app._get_current_object())
        return jsonify({'success': True, 'message': 'Optimierung abgeschlossen'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@api.route('/status')
def get_status():
    from apscheduler.schedulers.base import STATE_RUNNING
    account = Account.query.first()
    positions_count = Position.query.count()
    trades_count = Trade.query.count()
    stocks_count = Stock.query.filter_by(active=True).count()
    latest_signal = Signal.query.order_by(Signal.created_at.desc()).first()

    return jsonify({
        'ready': True,
        'stocks_loaded': stocks_count,
        'open_positions': positions_count,
        'total_trades': trades_count,
        'equity_eur': round(account.equity_eur, 2) if account else 0,
        'last_signal': latest_signal.created_at.isoformat() if latest_signal else None,
    })


# ─── Historical Simulations ─────────────────────────────────────────────────

@api.route('/simulations', methods=['GET'])
def get_simulations():
    runs = (SimulationRun.query
            .order_by(SimulationRun.created_at.desc())
            .all())
    return jsonify([run.to_dict() for run in runs])


@api.route('/simulations', methods=['POST'])
def create_simulation():
    from flask import current_app
    from services.replay_engine import create_simulation_run, run_historical_replay

    payload = request.get_json(silent=True) or {}
    required = ['start_date', 'end_date']
    missing = [field for field in required if not payload.get(field)]
    if missing:
        return jsonify({'success': False, 'error': f"Fehlende Felder: {', '.join(missing)}"}), 400

    try:
        run = create_simulation_run(payload)
        run_id = run.id
        auto_start = str(payload.get('auto_start', True)).lower() in ('1', 'true', 'yes', 'on')
        if auto_start:
            app_obj = current_app._get_current_object()

            def background_replay():
                try:
                    run_historical_replay(app_obj, run_id)
                except Exception:
                    log.exception("Hintergrund-Replay fehlgeschlagen fuer run_id=%s", run_id)

            thread = threading.Thread(
                target=background_replay,
                name=f"replay-run-{run_id}",
                daemon=True,
            )
            thread.start()
            run = SimulationRun.query.get(run_id)
        else:
            run = SimulationRun.query.get(run_id)
        return jsonify({
            'success': True,
            'auto_started': auto_start,
            'run': run.to_dict(),
        }), 201
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception as e:
        log.error(f"Simulation erstellen: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@api.route('/simulations/<int:run_id>', methods=['GET'])
def get_simulation(run_id):
    run = SimulationRun.query.get_or_404(run_id)
    latest_snapshot = (SimulationDailySnapshot.query
                       .filter_by(run_id=run_id)
                       .order_by(SimulationDailySnapshot.sim_date.desc())
                       .first())
    latest_decisions = (DecisionLog.query
                        .filter_by(run_id=run_id)
                        .order_by(DecisionLog.sim_date.desc(), DecisionLog.id.desc())
                        .limit(5)
                        .all())

    total_days = None
    processed_days = 0
    progress_pct = 0.0
    if run.start_date and run.end_date:
        total_days = max((run.end_date - run.start_date).days + 1, 1)
        if latest_snapshot and latest_snapshot.sim_date:
            processed_days = max((latest_snapshot.sim_date - run.start_date).days + 1, 0)
        elif run.current_date:
            processed_days = max((run.current_date - run.start_date).days, 0)
        processed_days = min(processed_days, total_days)
        progress_pct = round((processed_days / total_days) * 100, 1) if total_days else 0.0

    data = run.to_dict()
    data['latest_snapshot'] = latest_snapshot.to_dict() if latest_snapshot else None
    data['latest_decisions'] = [row.to_dict() for row in latest_decisions]
    data['progress'] = {
        'processed_days': processed_days,
        'total_days': total_days,
        'progress_pct': progress_pct,
        'current_date': run.current_date.isoformat() if run.current_date else None,
        'latest_snapshot_date': latest_snapshot.sim_date.isoformat() if latest_snapshot and latest_snapshot.sim_date else None,
    }
    return jsonify(data)


@api.route('/simulations/<int:run_id>', methods=['DELETE'])
def delete_simulation(run_id):
    run = SimulationRun.query.get_or_404(run_id)

    if str(run.status).upper() == 'RUNNING':
        return jsonify({
            'success': False,
            'error': 'Laufende Simulationen bitte erst abbrechen.'
        }), 409

    try:
        db.session.delete(run)
        db.session.commit()
        return jsonify({'success': True, 'deleted_run_id': run_id})
    except Exception as e:
        db.session.rollback()
        log.error(f"Simulation löschen: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@api.route('/simulations/<int:run_id>/cancel', methods=['POST'])
def cancel_simulation(run_id):
    run = SimulationRun.query.get_or_404(run_id)

    if str(run.status).upper() != 'RUNNING':
        return jsonify({
            'success': False,
            'error': 'Nur laufende Simulationen können abgebrochen werden.'
        }), 409

    try:
        run.status = 'cancel_requested'
        run.notes = ((run.notes or '').strip() + '\nCancel requested via UI').strip()
        db.session.commit()
        return jsonify({'success': True, 'run': run.to_dict()})
    except Exception as e:
        db.session.rollback()
        log.error(f"Simulation abbrechen: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@api.route('/simulations/<int:run_id>/equity', methods=['GET'])
def get_simulation_equity(run_id):
    rows = (SimulationDailySnapshot.query
            .filter_by(run_id=run_id)
            .order_by(SimulationDailySnapshot.sim_date.asc())
            .all())
    return jsonify([row.to_dict() for row in rows])


@api.route('/simulations/<int:run_id>/trades', methods=['GET'])
def get_simulation_trades(run_id):
    limit = min(int(request.args.get('limit', 300)), 1000)
    rows = (SimulationTrade.query
            .filter_by(run_id=run_id)
            .order_by(SimulationTrade.sim_date.desc(), SimulationTrade.id.desc())
            .limit(limit)
            .all())
    return jsonify([row.to_dict() for row in rows])


@api.route('/simulations/<int:run_id>/positions', methods=['GET'])
def get_simulation_positions(run_id):
    rows = (SimulationPosition.query
            .filter_by(run_id=run_id)
            .order_by(SimulationPosition.opened_at_sim_date.desc(), SimulationPosition.id.desc())
            .all())
    return jsonify([row.to_dict() for row in rows])


@api.route('/simulations/<int:run_id>/decisions', methods=['GET'])
def get_simulation_decisions(run_id):
    query = DecisionLog.query.filter_by(run_id=run_id)
    limit = min(int(request.args.get('limit', 400)), 1000)

    action = request.args.get('action')
    symbol = request.args.get('symbol')
    executed = request.args.get('executed')

    if action:
        query = query.filter(DecisionLog.action == action.upper())
    if symbol:
        query = query.join(Stock).filter(Stock.symbol == symbol)
    if executed is not None:
        query = query.filter(DecisionLog.executed == (executed.lower() == 'true'))

    rows = (query.order_by(DecisionLog.sim_date.desc(), DecisionLog.id.desc())
            .limit(limit)
            .all())
    return jsonify([row.to_dict() for row in rows])


@api.route('/simulations/<int:run_id>/metrics', methods=['GET'])
def get_simulation_metrics(run_id):
    run = SimulationRun.query.get_or_404(run_id)
    snapshots = (SimulationDailySnapshot.query
                 .filter_by(run_id=run_id)
                 .order_by(SimulationDailySnapshot.sim_date.asc())
                 .all())
    trades = (SimulationTrade.query
              .filter_by(run_id=run_id)
              .order_by(SimulationTrade.sim_date.asc(), SimulationTrade.id.asc())
              .all())
    sell_trades = [t for t in trades if t.action == 'SELL']

    latest_snapshot = snapshots[-1] if snapshots else None
    live_equity = latest_snapshot.equity_eur if latest_snapshot else run.initial_capital_eur
    live_total_return_pct = ((live_equity - run.initial_capital_eur) / run.initial_capital_eur * 100) if run.initial_capital_eur else 0.0
    live_max_drawdown_pct = max((s.drawdown_pct or 0.0) for s in snapshots) if snapshots else 0.0

    winning = [t for t in sell_trades if (t.pnl_eur or 0) > 0]
    losing = [t for t in sell_trades if (t.pnl_eur or 0) <= 0]
    live_win_rate = (len(winning) / len(sell_trades) * 100) if sell_trades else 0.0
    gross_profit = sum((t.pnl_eur or 0.0) for t in winning)
    gross_loss = abs(sum((t.pnl_eur or 0.0) for t in losing))
    live_profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (gross_profit if gross_profit > 0 else 0.0)

    daily_returns = []
    prev_equity = None
    for snapshot in snapshots:
        equity = snapshot.equity_eur or 0.0
        if prev_equity and prev_equity > 0:
            daily_returns.append((equity - prev_equity) / prev_equity)
        prev_equity = equity

    live_sharpe_ratio = 0.0
    if len(daily_returns) > 1:
        mean_return = sum(daily_returns) / len(daily_returns)
        variance = sum((r - mean_return) ** 2 for r in daily_returns) / (len(daily_returns) - 1)
        std_dev = (variance ** 0.5) if variance > 0 else 0.0
        if std_dev > 0:
            live_sharpe_ratio = mean_return / std_dev * (252 ** 0.5)

    benchmark_return_pct = run.benchmark_return_pct
    if benchmark_return_pct is None and latest_snapshot and run.start_date and run.end_date:
        total_days = max((run.end_date - run.start_date).days + 1, 1)
        processed_days = max((latest_snapshot.sim_date - run.start_date).days + 1, 1)
        processed_ratio = min(processed_days / total_days, 1.0)
        full_benchmark_return = _calculate_benchmark_return_until_date(run, latest_snapshot.sim_date)
        benchmark_return_pct = full_benchmark_return if full_benchmark_return is not None else processed_ratio * 0.0

    outperformance_pct = None
    if benchmark_return_pct is not None:
        outperformance_pct = round(live_total_return_pct - benchmark_return_pct, 2)

    decision_counts = {}
    for action, count in (
        db.session.query(DecisionLog.action, db.func.count(DecisionLog.id))
        .filter(DecisionLog.run_id == run_id)
        .group_by(DecisionLog.action)
        .all()
    ):
        decision_counts[action] = count

    executed_decisions = DecisionLog.query.filter_by(run_id=run_id, executed=True).count()

    return jsonify({
        'run_id': run.id,
        'status': run.status,
        'initial_capital_eur': round(run.initial_capital_eur, 2),
        'final_equity_eur': round((run.final_equity_eur if run.final_equity_eur is not None else live_equity) or 0.0, 2),
        'total_return_pct': round((run.total_return_pct if run.total_return_pct is not None else live_total_return_pct) or 0.0, 2),
        'benchmark_return_pct': round((run.benchmark_return_pct if run.benchmark_return_pct is not None else (benchmark_return_pct or 0.0)) or 0.0, 2),
        'outperformance_pct': outperformance_pct,
        'max_drawdown_pct': round((run.max_drawdown_pct if run.max_drawdown_pct is not None else live_max_drawdown_pct) or 0.0, 2),
        'sharpe_ratio': round((run.sharpe_ratio if run.sharpe_ratio is not None else live_sharpe_ratio) or 0.0, 4),
        'win_rate': round((run.win_rate if run.win_rate is not None else live_win_rate) or 0.0, 2),
        'profit_factor': round((run.profit_factor if run.profit_factor is not None else live_profit_factor) or 0.0, 4),
        'total_trades': run.total_trades if run.total_trades not in (None, 0) or str(run.status).lower() == 'completed' else len(trades),
        'winning_trades': run.winning_trades if run.winning_trades not in (None, 0) or str(run.status).lower() == 'completed' else len(winning),
        'losing_trades': run.losing_trades if run.losing_trades not in (None, 0) or str(run.status).lower() == 'completed' else len(losing),
        'decision_counts': decision_counts,
        'executed_decisions': executed_decisions,
        'live': {
            'equity_eur': round(live_equity or 0.0, 2),
            'latest_snapshot_date': latest_snapshot.sim_date.isoformat() if latest_snapshot and latest_snapshot.sim_date else None,
            'open_positions': latest_snapshot.open_positions if latest_snapshot else 0,
            'positions_value_eur': round((latest_snapshot.positions_value_eur if latest_snapshot else 0.0) or 0.0, 2),
            'cash_eur': round((latest_snapshot.cash_eur if latest_snapshot else run.initial_capital_eur) or 0.0, 2),
        },
    })


@api.route('/simulations/<int:run_id>/benchmark', methods=['GET'])
def get_simulation_benchmark(run_id):
    run = SimulationRun.query.get_or_404(run_id)
    rows = (SimulationDailySnapshot.query
            .filter_by(run_id=run_id)
            .order_by(SimulationDailySnapshot.sim_date.asc())
            .all())

    if not rows:
        return jsonify({
            'run_id': run.id,
            'benchmark_name': 'buy_and_hold_first_active_stock',
            'points': []
        })

    benchmark_points = []
    for row in rows:
        benchmark_return_pct = _calculate_benchmark_return_until_date(run, row.sim_date)
        benchmark_value = run.initial_capital_eur * (1 + ((benchmark_return_pct or 0.0) / 100.0))
        benchmark_points.append({
            'sim_date': row.sim_date.isoformat(),
            'value_eur': round(benchmark_value, 2),
        })

    return jsonify({
        'run_id': run.id,
        'benchmark_name': 'buy_and_hold_first_active_stock',
        'points': benchmark_points,
    })
