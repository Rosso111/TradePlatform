"""
Historical Replay Engine
Fuehrt historische Simulationen ohne Zukunftswissen aus und speichert
Runs, Decisions, Trades und Daily Snapshots.
"""
import logging
import math
from datetime import datetime, timezone, timedelta

from sqlalchemy import and_

import config
from models import (
    db,
    Stock,
    Price,
    SimulationRun,
    SimulationPosition,
    SimulationTrade,
    DecisionLog,
    SimulationDailySnapshot,
)
from services.algorithm import generate_signals_for_date
from services.trading_engine import (
    calc_commission,
    calc_spread_cost,
    calc_stop_loss,
    calc_take_profit,
)

log = logging.getLogger(__name__)


DEFAULT_STRATEGY_NAME = 'default_v1'
DEFAULT_STRATEGY_VERSION = '1.0'
DEFAULT_UNIVERSE_NAME = 'default_global_stocks'


def create_simulation_run(payload: dict) -> SimulationRun:
    start_date = _parse_date(payload['start_date'])
    end_date = _parse_date(payload['end_date'])
    if end_date < start_date:
        raise ValueError('end_date darf nicht vor start_date liegen')

    run = SimulationRun(
        name=payload.get('name') or f"Replay {payload.get('start_date')} – {payload.get('end_date')}",
        mode='historical_replay',
        status='queued',
        strategy_name=payload.get('strategy_name') or DEFAULT_STRATEGY_NAME,
        strategy_version=payload.get('strategy_version') or DEFAULT_STRATEGY_VERSION,
        universe_name=payload.get('universe_name') or DEFAULT_UNIVERSE_NAME,
        start_date=start_date,
        end_date=end_date,
        step_interval=payload.get('step_interval') or '1d',
        initial_capital_eur=float(payload.get('initial_capital_eur') or config.STARTING_CAPITAL),
        notes=payload.get('notes'),
    )
    db.session.add(run)
    db.session.commit()
    return run


def run_historical_replay(app, run_id: int) -> SimulationRun:
    with app.app_context():
        run = SimulationRun.query.get(run_id)
        if not run:
            raise ValueError(f"SimulationRun {run_id} nicht gefunden")

        if run.status == 'running':
            return run

        try:
            run.status = 'running'
            run.started_at = datetime.now(timezone.utc)
            run.current_date = run.start_date
            db.session.commit()

            cash_eur = run.initial_capital_eur
            peak_equity = run.initial_capital_eur

            sim_date = run.start_date
            while sim_date <= run.end_date:
                run.current_date = sim_date
                db.session.commit()

                _update_open_positions(run, sim_date)
                cash_eur = _get_run_cash(run)

                signals = generate_signals_for_date(app, sim_date)
                open_position_stock_ids = {
                    p.stock_id for p in SimulationPosition.query.filter_by(run_id=run.id).all()
                }

                for signal in signals:
                    if signal['action'] == 'BUY':
                        should_execute = True
                        skip_reason = None

                        if signal['stock_id'] in open_position_stock_ids:
                            should_execute = False
                            skip_reason = 'Position bereits offen'
                        elif len(open_position_stock_ids) >= config.MAX_POSITIONS:
                            should_execute = False
                            skip_reason = 'Maximale Anzahl Positionen erreicht'

                        latest_price_eur = signal['current_price_eur']
                        if latest_price_eur <= 0:
                            should_execute = False
                            skip_reason = 'Ungueltiger Preis'

                        budget_eur = min(cash_eur * 0.1, cash_eur)
                        if budget_eur < 100:
                            should_execute = False
                            skip_reason = 'Zu wenig Cash fuer neuen Trade'

                        commission = calc_commission(budget_eur) if budget_eur > 0 else 0.0
                        spread = calc_spread_cost(budget_eur) if budget_eur > 0 else 0.0
                        total_cost = budget_eur + commission + spread
                        if total_cost > cash_eur:
                            should_execute = False
                            skip_reason = 'Nicht genug Cash inkl. Kosten'

                        decision_log = _log_decision(
                            run,
                            signal,
                            sim_date,
                            executed=False,
                            execution_note=skip_reason,
                        )

                        if not should_execute:
                            continue

                        shares = budget_eur / latest_price_eur
                        stop_loss = calc_stop_loss(signal['current_price'], signal.get('atr'))
                        take_profit = calc_take_profit(signal['current_price'], stop_loss)
                        risk_distance = max(signal['current_price'] - stop_loss, 0)
                        signal['risk_json'] = {
                            'budget_eur': round(budget_eur, 2),
                            'stop_loss': round(stop_loss, 4),
                            'take_profit': round(take_profit, 4),
                            'risk_distance': round(risk_distance, 4),
                        }

                        position = SimulationPosition(
                            run_id=run.id,
                            stock_id=signal['stock_id'],
                            shares=shares,
                            entry_price=signal['current_price'],
                            entry_price_eur=signal['current_price_eur'],
                            current_price=signal['current_price'],
                            current_price_eur=signal['current_price_eur'],
                            stop_loss=stop_loss,
                            take_profit=take_profit,
                            trailing_stop=stop_loss,
                            highest_price=signal['current_price'],
                            cost_eur=total_cost,
                            commission_eur=commission,
                            opened_at_sim_date=sim_date,
                            reason=signal.get('reason', ''),
                        )
                        db.session.add(position)
                        db.session.flush()

                        trade = SimulationTrade(
                            run_id=run.id,
                            stock_id=signal['stock_id'],
                            action='BUY',
                            sim_date=sim_date,
                            shares=shares,
                            price=signal['current_price'],
                            price_eur=signal['current_price_eur'],
                            fx_rate=1.0,
                            commission_eur=commission,
                            spread_eur=spread,
                            total_eur=total_cost,
                            pnl_eur=0.0,
                            pnl_pct=0.0,
                            reason=signal.get('reason', ''),
                            decision_log_id=decision_log.id,
                        )
                        db.session.add(trade)
                        decision_log.executed = True
                        decision_log.execution_note = 'Trade ausgefuehrt'
                        cash_eur -= total_cost
                        open_position_stock_ids.add(signal['stock_id'])
                        db.session.commit()

                    elif signal['action'] == 'SELL' and signal['stock_id'] in open_position_stock_ids:
                        position = SimulationPosition.query.filter_by(
                            run_id=run.id,
                            stock_id=signal['stock_id']
                        ).first()
                        decision_log = _log_decision(run, signal, sim_date, executed=bool(position))
                        if position:
                            cash_eur += _close_position(
                                run, position, sim_date, signal['reason'], decision_log_id=decision_log.id
                            )
                            open_position_stock_ids.discard(signal['stock_id'])

                positions_value = _get_positions_value(run, sim_date)
                equity = cash_eur + positions_value
                peak_equity = max(peak_equity, equity)
                drawdown_pct = ((peak_equity - equity) / peak_equity * 100) if peak_equity > 0 else 0.0

                prev_snapshot = (SimulationDailySnapshot.query
                                 .filter(
                                     SimulationDailySnapshot.run_id == run.id,
                                     SimulationDailySnapshot.sim_date < sim_date
                                 )
                                 .order_by(SimulationDailySnapshot.sim_date.desc())
                                 .first())
                prev_equity = prev_snapshot.equity_eur if prev_snapshot else run.initial_capital_eur
                daily_pnl = equity - prev_equity

                snapshot = SimulationDailySnapshot(
                    run_id=run.id,
                    sim_date=sim_date,
                    cash_eur=round(cash_eur, 2),
                    positions_value_eur=round(positions_value, 2),
                    equity_eur=round(equity, 2),
                    daily_pnl_eur=round(daily_pnl, 2),
                    drawdown_pct=round(drawdown_pct, 2),
                    open_positions=len(open_position_stock_ids),
                )
                db.session.add(snapshot)
                db.session.commit()

                sim_date += timedelta(days=1)

            _finalize_run(run)
            return run

        except Exception as e:
            db.session.rollback()
            log.exception("Replay-Run fehlgeschlagen")
            run = SimulationRun.query.get(run_id)
            if run:
                run.status = 'failed'
                run.error_message = str(e)
                run.finished_at = datetime.now(timezone.utc)
                db.session.commit()
            raise


def _update_open_positions(run: SimulationRun, sim_date):
    positions = SimulationPosition.query.filter_by(run_id=run.id).all()
    for position in positions:
        latest = (Price.query
                  .filter(and_(Price.stock_id == position.stock_id, Price.date <= sim_date))
                  .order_by(Price.date.desc())
                  .first())
        if not latest:
            continue

        position.current_price = latest.close
        position.current_price_eur = latest.close_eur or latest.close

        if latest.close > (position.highest_price or position.entry_price):
            position.highest_price = latest.close
            trailing = latest.close * (1 - config.TRAILING_STOP_PCT)
            if trailing > (position.trailing_stop or 0):
                position.trailing_stop = trailing

        effective_stop = max(position.stop_loss or 0, position.trailing_stop or 0)
        if effective_stop > 0 and latest.close <= effective_stop:
            _close_position(run, position, sim_date, 'Stop-Loss ausgelöst')
            continue

        if position.take_profit and latest.close >= position.take_profit:
            _close_position(run, position, sim_date, 'Take-Profit erreicht')
            continue

    db.session.commit()


def _close_position(run: SimulationRun, position: SimulationPosition, sim_date, reason: str, decision_log_id=None) -> float:
    revenue = position.shares * (position.current_price_eur or position.entry_price_eur)
    commission = calc_commission(revenue)
    spread = calc_spread_cost(revenue)
    net_revenue = revenue - commission - spread

    cost_basis = position.shares * position.entry_price_eur
    pnl_eur = net_revenue - cost_basis
    pnl_pct = (pnl_eur / cost_basis * 100) if cost_basis > 0 else 0.0

    trade = SimulationTrade(
        run_id=run.id,
        stock_id=position.stock_id,
        action='SELL',
        sim_date=sim_date,
        shares=position.shares,
        price=position.current_price or position.entry_price,
        price_eur=position.current_price_eur or position.entry_price_eur,
        fx_rate=1.0,
        commission_eur=commission,
        spread_eur=spread,
        total_eur=net_revenue,
        pnl_eur=pnl_eur,
        pnl_pct=pnl_pct,
        reason=reason,
        decision_log_id=decision_log_id,
    )
    db.session.add(trade)
    db.session.delete(position)
    db.session.commit()
    return net_revenue


def _log_decision(run: SimulationRun, signal: dict, sim_date, executed: bool, execution_note=None):
    reason_json = signal.get('reason_json') or {
        'summary': signal.get('reason', ''),
        'technical': {
            'rsi': signal.get('rsi'),
            'atr': signal.get('atr'),
        },
    }
    risk_json = signal.get('risk_json') or {}
    if execution_note:
        risk_json = {**risk_json, 'execution_note': execution_note}

    log_entry = DecisionLog(
        run_id=run.id,
        stock_id=signal['stock_id'],
        sim_date=sim_date,
        action=signal['action'],
        final_score=signal.get('score', 0.0),
        technical_score=signal.get('technical_score'),
        analyst_score=signal.get('analyst_score'),
        news_score=signal.get('news_score'),
        sector_score=signal.get('sector_score'),
        risk_score=signal.get('risk_score'),
        current_price=signal.get('current_price'),
        current_price_eur=signal.get('current_price_eur'),
        atr=signal.get('atr'),
        rsi=signal.get('rsi'),
        macd=signal.get('macd'),
        ema_fast=signal.get('ema_fast'),
        ema_slow=signal.get('ema_slow'),
        reason_summary=signal.get('reason', ''),
        reason_json=reason_json,
        risk_json=risk_json,
        data_snapshot_json=signal.get('data_snapshot_json') or {'sim_date': sim_date.isoformat()},
        executed=executed,
    )
    db.session.add(log_entry)
    db.session.commit()
    return log_entry


def _get_positions_value(run: SimulationRun, sim_date) -> float:
    positions = SimulationPosition.query.filter_by(run_id=run.id).all()
    total = 0.0
    for position in positions:
        latest = (Price.query
                  .filter(and_(Price.stock_id == position.stock_id, Price.date <= sim_date))
                  .order_by(Price.date.desc())
                  .first())
        if latest:
            total += (latest.close_eur or latest.close) * position.shares
        else:
            total += position.entry_price_eur * position.shares
    return total


def _get_run_cash(run: SimulationRun) -> float:
    buys = (db.session.query(db.func.coalesce(db.func.sum(SimulationTrade.total_eur), 0.0))
            .filter_by(run_id=run.id, action='BUY')
            .scalar())
    sells = (db.session.query(db.func.coalesce(db.func.sum(SimulationTrade.total_eur), 0.0))
             .filter_by(run_id=run.id, action='SELL')
             .scalar())
    return float(run.initial_capital_eur + sells - buys)


def _finalize_run(run: SimulationRun):
    snapshots = (SimulationDailySnapshot.query
                 .filter_by(run_id=run.id)
                 .order_by(SimulationDailySnapshot.sim_date.asc())
                 .all())
    trades = SimulationTrade.query.filter_by(run_id=run.id).all()
    sell_trades = [t for t in trades if t.action == 'SELL']

    final_equity = snapshots[-1].equity_eur if snapshots else run.initial_capital_eur
    total_return_pct = ((final_equity - run.initial_capital_eur) / run.initial_capital_eur * 100) if run.initial_capital_eur > 0 else 0.0
    benchmark_return_pct = _calculate_buy_and_hold_benchmark(run)
    winning = [t for t in sell_trades if (t.pnl_eur or 0) > 0]
    losing = [t for t in sell_trades if (t.pnl_eur or 0) <= 0]
    win_rate = (len(winning) / len(sell_trades) * 100) if sell_trades else 0.0

    gross_profit = sum((t.pnl_eur or 0.0) for t in winning)
    gross_loss = abs(sum((t.pnl_eur or 0.0) for t in losing))
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (gross_profit if gross_profit > 0 else 0.0)
    max_drawdown_pct = max((s.drawdown_pct or 0.0) for s in snapshots) if snapshots else 0.0

    daily_returns = []
    prev_equity = None
    for snapshot in snapshots:
        equity = snapshot.equity_eur or 0.0
        if prev_equity and prev_equity > 0:
            daily_returns.append((equity - prev_equity) / prev_equity)
        prev_equity = equity

    sharpe_ratio = 0.0
    if len(daily_returns) > 1:
        mean_return = sum(daily_returns) / len(daily_returns)
        variance = sum((r - mean_return) ** 2 for r in daily_returns) / (len(daily_returns) - 1)
        std_dev = math.sqrt(variance) if variance > 0 else 0.0
        if std_dev > 0:
            sharpe_ratio = mean_return / std_dev * math.sqrt(252)

    run.status = 'completed'
    run.finished_at = datetime.now(timezone.utc)
    run.final_equity_eur = round(final_equity, 2)
    run.total_return_pct = round(total_return_pct, 2)
    run.benchmark_return_pct = round(benchmark_return_pct, 2)
    run.max_drawdown_pct = round(max_drawdown_pct, 2)
    run.sharpe_ratio = round(sharpe_ratio, 4)
    run.win_rate = round(win_rate, 2)
    run.profit_factor = round(profit_factor, 4)
    run.total_trades = len(trades)
    run.winning_trades = len(winning)
    run.losing_trades = len(losing)
    db.session.commit()


def _calculate_buy_and_hold_benchmark(run: SimulationRun) -> float:
    first_stock = Stock.query.filter_by(active=True).order_by(Stock.symbol.asc()).first()
    if not first_stock:
        return 0.0

    start_price = (Price.query
                   .filter(Price.stock_id == first_stock.id, Price.date >= run.start_date)
                   .order_by(Price.date.asc())
                   .first())
    end_price = (Price.query
                 .filter(Price.stock_id == first_stock.id, Price.date <= run.end_date)
                 .order_by(Price.date.desc())
                 .first())

    if not start_price or not end_price:
        return 0.0

    start_eur = start_price.close_eur or start_price.close
    end_eur = end_price.close_eur or end_price.close
    if not start_eur or start_eur <= 0:
        return 0.0

    return (end_eur - start_eur) / start_eur * 100


def _parse_date(value):
    if hasattr(value, 'year'):
        return value
    return datetime.strptime(value, '%Y-%m-%d').date()
