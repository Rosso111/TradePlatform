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
    AlgoParams,
    SimulationRun,
    SimulationPosition,
    SimulationTrade,
    DecisionLog,
    SimulationDailySnapshot,
)
import pandas as pd
from services.algorithm import add_indicators, compute_score, _build_reason, _default_params
from services.strategy_store import get_strategy
from services.universe_store import get_universe
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

    selected_strategy_id = payload.get('strategy_id') or payload.get('strategy_name') or DEFAULT_STRATEGY_NAME

    run = SimulationRun(
        name=payload.get('name') or f"Replay {payload.get('start_date')} – {payload.get('end_date')}",
        mode='historical_replay',
        status='queued',
        strategy_name=selected_strategy_id,
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


DECISION_LOG_SAMPLE_INTERVAL_DAYS = 5
REPLAY_COMMIT_INTERVAL_DAYS = 5


def _safe_float(value):
    if value is None or pd.isna(value):
        return None
    return float(value)


def _compute_score_fast(row: dict, params: dict, analyst_score: float = 50.0, sector_score: float = 50.0) -> float:
    score = 0.0

    rsi = _safe_float(row.get('rsi'))
    if rsi is not None:
        oversold = params.get('rsi_oversold', 35)
        overbought = params.get('rsi_overbought', 65)
        if rsi < oversold:
            rsi_score = 80 + (oversold - rsi) / oversold * 20
        elif rsi > overbought:
            rsi_score = 20 - (rsi - overbought) / (100 - overbought) * 20
        else:
            rsi_score = 50 + (50 - rsi) * 0.5
        score += min(max(rsi_score, 0), 100) * 0.25

    macd = _safe_float(row.get('macd'))
    macd_sig = _safe_float(row.get('macd_signal'))
    macd_hist = _safe_float(row.get('macd_hist'))
    if macd is not None and macd_sig is not None and macd_hist is not None:
        if macd > macd_sig:
            macd_score = 65 + min(abs(macd_hist) / max(abs(macd), 0.001) * 35, 35)
        else:
            macd_score = 35 - min(abs(macd_hist) / max(abs(macd), 0.001) * 35, 35)
        score += min(max(macd_score, 0), 100) * 0.20

    ema_f = _safe_float(row.get('ema_fast'))
    ema_s = _safe_float(row.get('ema_slow'))
    close = _safe_float(row.get('Close'))
    if ema_f is not None and ema_s is not None and close is not None:
        if ema_f > ema_s:
            gap_pct = (ema_f - ema_s) / ema_s * 100
            ema_score = 60 + min(gap_pct * 10, 40)
        else:
            gap_pct = (ema_s - ema_f) / ema_s * 100
            ema_score = 40 - min(gap_pct * 10, 40)
        ema_score = min(ema_score + 5, 100) if close > ema_f else max(ema_score - 5, 0)
        score += min(max(ema_score, 0), 100) * 0.20

    bb_up = _safe_float(row.get('bb_upper'))
    bb_lo = _safe_float(row.get('bb_lower'))
    bb_mid = _safe_float(row.get('bb_mid'))
    if bb_up is not None and bb_lo is not None and bb_mid is not None and close is not None:
        band_width = bb_up - bb_lo
        if band_width > 0:
            position = (close - bb_lo) / band_width
            if position < 0.2:
                bb_score = 80 + (0.2 - position) / 0.2 * 20
            elif position > 0.8:
                bb_score = 20 - (position - 0.8) / 0.2 * 20
            else:
                bb_score = 50
        else:
            bb_score = 50
        score += min(max(bb_score, 0), 100) * 0.15

    score += analyst_score * 0.10
    score += sector_score * 0.10
    return min(max(score, 0), 100)


def _should_persist_decision(signal: dict, sim_date) -> bool:
    action = (signal.get('action') or 'HOLD').upper()
    if action in ('BUY', 'SELL'):
        return True

    score = float(signal.get('score') or 0.0)
    if score >= 60 or score <= 40:
        return True

    return (sim_date.toordinal() % DECISION_LOG_SAMPLE_INTERVAL_DAYS) == 0


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
            previous_equity = run.initial_capital_eur
            replay_data = _build_replay_data_cache(run)
            benchmark_return_pct = _calculate_benchmark_return_until_date(run, run.end_date, replay_data)

            sim_date = run.start_date
            days_since_commit = 0
            while sim_date <= run.end_date:
                db.session.refresh(run)
                if str(run.status).lower() == 'cancel_requested':
                    db.session.flush()
                    run.status = 'cancelled'
                    run.finished_at = datetime.now(timezone.utc)
                    db.session.commit()
                    return run

                run.current_date = sim_date

                positions = SimulationPosition.query.filter_by(run_id=run.id).all()
                positions_by_stock_id = {p.stock_id: p for p in positions if p.id is not None and p.shares > 0}
                cash_delta = _update_open_positions(run, sim_date, replay_data, positions)
                cash_eur += cash_delta
                positions_by_stock_id = {p.stock_id: p for p in positions if p.id is not None and p.shares > 0}

                signals = _generate_signals_from_cache(run, sim_date, replay_data, include_details=True)
                open_position_stock_ids = set(positions_by_stock_id.keys())

                signal_counts = {'BUY': 0, 'SELL': 0, 'HOLD': 0, 'SKIP': 0}

                for signal in signals:
                    action = signal.get('action', 'HOLD')
                    signal_counts[action] = signal_counts.get(action, 0) + 1

                    if action == 'SKIP':
                        if _should_persist_decision(signal, sim_date):
                            _log_decision(
                                run,
                                signal,
                                sim_date,
                                executed=False,
                                execution_note=signal.get('skip_reason') or 'Gefiltert',
                                flush=False,
                            )
                        continue

                    if action == 'HOLD':
                        if _should_persist_decision(signal, sim_date):
                            _log_decision(
                                run,
                                signal,
                                sim_date,
                                executed=False,
                                execution_note='Kein Trade-Signal',
                                flush=False,
                            )
                        continue

                    if action == 'BUY':
                        should_execute = True
                        skip_reason = None

                        if signal['stock_id'] in open_position_stock_ids:
                            should_execute = False
                            skip_reason = 'Position bereits offen'
                        max_positions = replay_data.get('strategy_params', {}).get('max_positions', config.MAX_POSITIONS)
                        if len(open_position_stock_ids) >= max_positions:
                            should_execute = False
                            skip_reason = 'Maximale Anzahl Positionen erreicht'

                        latest_price_eur = signal['current_price_eur']
                        if latest_price_eur <= 0:
                            should_execute = False
                            skip_reason = 'Ungueltiger Preis'

                        max_position_size = replay_data.get('strategy_params', {}).get('max_position_size', 0.1)
                        min_position_size = replay_data.get('strategy_params', {}).get('min_position_size', 0.03)
                        budget_eur = min(cash_eur * max_position_size, cash_eur)
                        budget_eur = max(budget_eur, run.initial_capital_eur * min_position_size)
                        budget_eur = min(budget_eur, cash_eur)
                        if budget_eur < 100:
                            should_execute = False
                            skip_reason = 'Zu wenig Cash fuer neuen Trade'

                        commission = calc_commission(budget_eur) if budget_eur > 0 else 0.0
                        spread = calc_spread_cost(budget_eur) if budget_eur > 0 else 0.0
                        total_cost = budget_eur + commission + spread
                        if total_cost > cash_eur:
                            should_execute = False
                            skip_reason = 'Nicht genug Cash inkl. Kosten'

                        if not should_execute:
                            if _should_persist_decision(signal, sim_date):
                                _log_decision(
                                    run,
                                    signal,
                                    sim_date,
                                    executed=False,
                                    execution_note=skip_reason,
                                    flush=False,
                                )
                            signal_counts['SKIP'] += 1
                            continue

                        decision_log = _log_decision(
                            run,
                            signal,
                            sim_date,
                            executed=False,
                            execution_note=skip_reason,
                            flush=True,
                        )

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
                        cash_eur -= total_cost
                        open_position_stock_ids.add(signal['stock_id'])
                        continue

                    if action == 'SELL':
                        position = positions_by_stock_id.get(signal['stock_id'])
                        strategy_params = replay_data.get('strategy_params', {}) if replay_data else {}
                        can_sell = bool(position)
                        skip_reason = None if position else 'Keine offene Position zum Verkaufen'
                        if position:
                            can_sell, skip_reason = _can_sell_position(position, strategy_params, signal.get('reason', ''))

                        if position and can_sell:
                            decision_log = _log_decision(
                                run,
                                signal,
                                sim_date,
                                executed=True,
                                execution_note=skip_reason,
                                flush=True,
                            )
                            cash_eur += _close_position(
                                run, position, sim_date, signal['reason'], decision_log_id=decision_log.id
                            )
                            open_position_stock_ids.discard(signal['stock_id'])
                            positions_by_stock_id.pop(signal['stock_id'], None)
                        else:
                            if _should_persist_decision(signal, sim_date):
                                _log_decision(
                                    run,
                                    signal,
                                    sim_date,
                                    executed=False,
                                    execution_note=skip_reason,
                                    flush=False,
                                )
                            signal_counts['SKIP'] += 1

                log.info(
                    'Replay %s %s: signals=%s open_positions=%s cash=%.2f',
                    run.id,
                    sim_date.isoformat(),
                    signal_counts,
                    len(open_position_stock_ids),
                    cash_eur,
                )

                positions_value = _get_positions_value(run, sim_date, replay_data, positions)
                equity = cash_eur + positions_value
                peak_equity = max(peak_equity, equity)
                drawdown_pct = ((peak_equity - equity) / peak_equity * 100) if peak_equity > 0 else 0.0

                daily_pnl = equity - previous_equity

                snapshot = (SimulationDailySnapshot.query
                            .filter_by(run_id=run.id, sim_date=sim_date)
                            .first())
                if not snapshot:
                    snapshot = SimulationDailySnapshot(
                        run_id=run.id,
                        sim_date=sim_date,
                    )
                    db.session.add(snapshot)

                snapshot.cash_eur = round(cash_eur, 2)
                snapshot.positions_value_eur = round(positions_value, 2)
                snapshot.equity_eur = round(equity, 2)
                snapshot.daily_pnl_eur = round(daily_pnl, 2)
                snapshot.drawdown_pct = round(drawdown_pct, 2)
                snapshot.open_positions = len(open_position_stock_ids)
                days_since_commit += 1
                should_commit_now = days_since_commit >= REPLAY_COMMIT_INTERVAL_DAYS or sim_date >= run.end_date

                if should_commit_now:
                    db.session.commit()
                    db.session.expire_all()
                    days_since_commit = 0
                else:
                    db.session.flush()

                previous_equity = equity
                sim_date += timedelta(days=1)

            _finalize_run(run, benchmark_return_pct=benchmark_return_pct)
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


def _update_open_positions(run: SimulationRun, sim_date, replay_data=None, positions=None):
    positions = positions if positions is not None else SimulationPosition.query.filter_by(run_id=run.id).all()
    cash_delta = 0.0
    strategy_params = (replay_data or {}).get('strategy_params', {})
    trailing_stop_pct = strategy_params.get('trailing_stop_pct', config.TRAILING_STOP_PCT)
    trim_position_above_eur = strategy_params.get('trim_position_above_eur')
    trim_fraction = strategy_params.get('trim_fraction', 0.5)
    sideways_days = strategy_params.get('sideways_days')
    sideways_band_pct = strategy_params.get('sideways_band_pct')
    for position in positions:
        latest = _get_cached_price(position.stock_id, sim_date, replay_data)
        if latest is None:
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
            trailing = latest.close * (1 - trailing_stop_pct)
            if trailing > (position.trailing_stop or 0):
                position.trailing_stop = trailing

        position_value_eur = (position.current_price_eur or position.entry_price_eur) * position.shares
        already_trimmed = 'trimmed_once' in (position.reason or '')
        if trim_position_above_eur and position_value_eur > trim_position_above_eur and not already_trimmed:
            cash_delta += _trim_position(run, position, sim_date, trim_fraction, 'Teilverkauf > 4000 EUR')
            position.reason = ((position.reason or '') + ' | trimmed_once').strip(' |')
            continue

        if sideways_days and sideways_band_pct and position.opened_at_sim_date:
            if (sim_date - position.opened_at_sim_date).days >= int(sideways_days):
                history = _get_position_price_window(position.stock_id, sim_date, int(sideways_days), replay_data)
                if history:
                    closes = [p.close_eur or p.close for p in history if (p.close_eur or p.close)]
                    if closes:
                        band_pct = (max(closes) - min(closes)) / max(min(closes), 0.0001)
                        if band_pct <= float(sideways_band_pct):
                            can_sell, _ = _can_sell_position(position, strategy_params, 'Seitwärtsphase > 30 Tage')
                            if can_sell:
                                cash_delta += _close_position(run, position, sim_date, 'Seitwärtsphase > 30 Tage')
                                continue

        effective_stop = max(position.stop_loss or 0, position.trailing_stop or 0)
        if effective_stop > 0 and latest.close <= effective_stop:
            cash_delta += _close_position(run, position, sim_date, 'Stop-Loss ausgelöst')
            continue

        if position.take_profit and latest.close >= position.take_profit:
            cash_delta += _close_position(run, position, sim_date, 'Take-Profit erreicht')
            continue

    return cash_delta



def _position_profit_pct(position: SimulationPosition) -> float:
    current_value = position.shares * (position.current_price_eur or position.entry_price_eur)
    cost_basis = position.shares * position.entry_price_eur
    if cost_basis <= 0:
        return 0.0
    return ((current_value - cost_basis) / cost_basis) * 100


def _can_sell_position(position: SimulationPosition, strategy_params: dict, reason: str) -> tuple[bool, str | None]:
    normalized_reason = (reason or '').lower()
    rule_mode = strategy_params.get('min_profit_rule_mode', 'strategy_only')

    if 'seitwärtsphase' in normalized_reason or 'seitwaertsphase' in normalized_reason:
        min_profit_pct = strategy_params.get(
            'min_profit_pct_for_sideways_exit',
            strategy_params.get('min_profit_pct_for_sell')
        )
    else:
        min_profit_pct = strategy_params.get('min_profit_pct_for_sell')

    if min_profit_pct in (None, '', False):
        return True, None

    if rule_mode == 'strategy_only':
        bypass_terms = ('stop-loss', 'stop loss', 'take-profit', 'trailing')
        if any(term in normalized_reason for term in bypass_terms):
            return True, None

    profit_pct = _position_profit_pct(position)
    if profit_pct < float(min_profit_pct):
        return False, f'Mindestgewinn {float(min_profit_pct):.1f}% noch nicht erreicht ({profit_pct:.1f}%)'

    return True, None


def _trim_position(run: SimulationRun, position: SimulationPosition, sim_date, fraction: float, reason: str) -> float:
    fraction = min(max(fraction, 0.0), 1.0)
    if fraction <= 0 or position.shares <= 0:
        return 0.0

    shares_to_sell = position.shares * fraction
    revenue = shares_to_sell * (position.current_price_eur or position.entry_price_eur)
    commission = calc_commission(revenue)
    spread = calc_spread_cost(revenue)
    net_revenue = revenue - commission - spread

    cost_basis = shares_to_sell * position.entry_price_eur
    pnl_eur = net_revenue - cost_basis
    pnl_pct = (pnl_eur / cost_basis * 100) if cost_basis > 0 else 0.0

    trade = SimulationTrade(
        run_id=run.id,
        stock_id=position.stock_id,
        action='SELL',
        sim_date=sim_date,
        shares=shares_to_sell,
        price=position.current_price or position.entry_price,
        price_eur=position.current_price_eur or position.entry_price_eur,
        fx_rate=1.0,
        commission_eur=commission,
        spread_eur=spread,
        total_eur=net_revenue,
        pnl_eur=pnl_eur,
        pnl_pct=pnl_pct,
        reason=reason,
        decision_log_id=None,
    )
    db.session.add(trade)
    position.shares -= shares_to_sell
    return net_revenue


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
    return net_revenue


def _log_decision(run: SimulationRun, signal: dict, sim_date, executed: bool, execution_note=None, flush: bool = False):
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
    if flush:
        db.session.flush()
    return log_entry


def _get_positions_value(run: SimulationRun, sim_date, replay_data=None, positions=None) -> float:
    positions = positions if positions is not None else SimulationPosition.query.filter_by(run_id=run.id).all()
    total = 0.0
    for position in positions:
        latest = _get_cached_price(position.stock_id, sim_date, replay_data)
        if latest is None:
            latest = (Price.query
                      .filter(and_(Price.stock_id == position.stock_id, Price.date <= sim_date))
                      .order_by(Price.date.desc())
                      .first())
        if latest:
            total += (latest.close_eur or latest.close) * position.shares
        else:
            total += position.entry_price_eur * position.shares
    return total


def _get_position_price_window(stock_id: int, sim_date, days: int, replay_data=None):
    if replay_data:
        prices = replay_data.get('prices_by_stock', {}).get(stock_id, [])
        start_date = sim_date - timedelta(days=days)
        return [p for p in prices if start_date <= p.date <= sim_date]
    return []


def _get_run_cash(run: SimulationRun) -> float:
    buys = (db.session.query(db.func.coalesce(db.func.sum(SimulationTrade.total_eur), 0.0))
            .filter_by(run_id=run.id, action='BUY')
            .scalar())
    sells = (db.session.query(db.func.coalesce(db.func.sum(SimulationTrade.total_eur), 0.0))
             .filter_by(run_id=run.id, action='SELL')
             .scalar())
    return float(run.initial_capital_eur + sells - buys)


def _finalize_run(run: SimulationRun, benchmark_return_pct: float | None = None):
    snapshots = (SimulationDailySnapshot.query
                 .filter_by(run_id=run.id)
                 .order_by(SimulationDailySnapshot.sim_date.asc())
                 .all())
    trades = SimulationTrade.query.filter_by(run_id=run.id).all()
    sell_trades = [t for t in trades if t.action == 'SELL']

    final_equity = snapshots[-1].equity_eur if snapshots else run.initial_capital_eur
    total_return_pct = ((final_equity - run.initial_capital_eur) / run.initial_capital_eur * 100) if run.initial_capital_eur > 0 else 0.0
    benchmark_return_pct = benchmark_return_pct if benchmark_return_pct is not None else _calculate_buy_and_hold_benchmark(run)
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

    if str(run.status).lower() == 'cancel_requested':
        run.status = 'cancelled'
    else:
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
    return _calculate_benchmark_return_until_date(run, run.end_date) or 0.0


def _get_benchmark_series(run: SimulationRun, replay_data: dict | None = None):
    first_stock = None
    if replay_data:
        stocks = replay_data.get('stocks', [])
        if stocks:
            first_stock = sorted(stocks, key=lambda stock: stock.symbol)[0]
            prices = replay_data.get('prices_by_stock', {}).get(first_stock.id, [])
            return first_stock, prices

    first_stock = Stock.query.filter_by(active=True).order_by(Stock.symbol.asc()).first()
    if not first_stock:
        return None, []

    prices = (Price.query
              .filter(Price.stock_id == first_stock.id, Price.date <= run.end_date)
              .order_by(Price.date.asc())
              .all())
    return first_stock, prices


def _calculate_benchmark_return_until_date(run: SimulationRun, end_date, replay_data: dict | None = None) -> float | None:
    first_stock, prices = _get_benchmark_series(run, replay_data)
    if not first_stock or not prices:
        return 0.0

    start_price = next((price for price in prices if price.date >= run.start_date), None)
    end_price = None
    for price in prices:
        if price.date <= end_date:
            end_price = price
        else:
            break

    if not start_price or not end_price:
        return None

    start_eur = start_price.close_eur or start_price.close
    end_eur = end_price.close_eur or end_price.close
    if not start_eur or start_eur <= 0:
        return None

    return (end_eur - start_eur) / start_eur * 100


def _build_replay_data_cache(run: SimulationRun) -> dict:
    strategy = get_strategy(run.strategy_name) or {'id': DEFAULT_STRATEGY_NAME, 'mode': 'score', 'params': {}}
    strategy_params = strategy.get('params', {}) or {}
    strategy_mode = strategy.get('mode') or 'score'

    universe = get_universe(run.universe_name) or {'id': DEFAULT_UNIVERSE_NAME, 'symbols': []}
    universe_symbols = [symbol for symbol in (universe.get('symbols') or []) if symbol]

    if universe_symbols:
        stocks = (Stock.query
                  .filter(Stock.active.is_(True), Stock.symbol.in_(universe_symbols))
                  .all())
        order_map = {symbol: idx for idx, symbol in enumerate(universe_symbols)}
        stocks = sorted(stocks, key=lambda stock: order_map.get(stock.symbol, 999999))
    else:
        stocks = Stock.query.filter_by(active=True).all()

    stock_ids = [stock.id for stock in stocks]
    prices = (Price.query
              .filter(Price.stock_id.in_(stock_ids), Price.date <= run.end_date)
              .order_by(Price.stock_id.asc(), Price.date.asc())
              .all()) if stock_ids else []

    prices_by_stock = {}
    frames_by_stock = {}
    rows_by_stock = {}
    row_index_by_stock = {}
    params_by_stock = {}
    stock_meta = {stock.id: stock for stock in stocks}

    algo_rows = AlgoParams.query.filter(AlgoParams.stock_id.in_(stock_ids)).all() if stock_ids else []
    algo_by_stock = {row.stock_id: row for row in algo_rows}

    for price in prices:
      prices_by_stock.setdefault(price.stock_id, []).append(price)

    sector_changes = {}
    for stock in stocks:
        stock_prices = prices_by_stock.get(stock.id, [])
        if len(stock_prices) >= 2:
            recent = stock_prices[-25:]
            oldest = recent[0].close_eur or recent[0].close
            newest = recent[-1].close_eur or recent[-1].close
            if oldest and oldest > 0:
                sector_changes.setdefault(stock.sector, []).append((newest - oldest) / oldest * 100)

        if len(stock_prices) < 60:
            continue

        algo = algo_by_stock.get(stock.id)
        params = (
            {
                'rsi_period': algo.rsi_period, 'rsi_oversold': algo.rsi_oversold,
                'rsi_overbought': algo.rsi_overbought, 'ema_fast': algo.ema_fast,
                'ema_slow': algo.ema_slow, 'macd_fast': algo.macd_fast,
                'macd_slow': algo.macd_slow, 'macd_signal': algo.macd_signal,
                'bb_period': algo.bb_period, 'bb_std': algo.bb_std,
            }
            if algo else _default_params()
        )
        params_by_stock[stock.id] = params

        df = pd.DataFrame([
            {
                'date': p.date,
                'Open': p.open,
                'High': p.high,
                'Low': p.low,
                'Close': p.close,
                'CloseEUR': p.close_eur or p.close,
                'Volume': p.volume,
            }
            for p in stock_prices
        ]).set_index('date')
        df = add_indicators(df, params)
        frames_by_stock[stock.id] = df

        valid_df = df.dropna()
        if not valid_df.empty:
            rows_by_stock[stock.id] = valid_df.to_dict('records')
            row_index_by_stock[stock.id] = {
                idx_date: idx
                for idx, idx_date in enumerate(valid_df.index.tolist())
            }

    sector_scores = {}
    for sector, changes in sector_changes.items():
        avg_change = sum(changes) / len(changes)
        sector_scores[sector] = min(max(50 + avg_change * 3, 10), 90)

    return {
        'stocks': stocks,
        'universe': universe,
        'universe_symbols': universe_symbols,
        'stock_meta': stock_meta,
        'prices_by_stock': prices_by_stock,
        'frames_by_stock': frames_by_stock,
        'rows_by_stock': rows_by_stock,
        'row_index_by_stock': row_index_by_stock,
        'params_by_stock': params_by_stock,
        'sector_scores': sector_scores,
        'strategy': strategy,
        'strategy_mode': strategy_mode,
        'strategy_params': strategy_params,
    }


def _get_cached_price(stock_id: int, sim_date, replay_data=None):
    if not replay_data:
        return None
    prices = replay_data.get('prices_by_stock', {}).get(stock_id, [])
    latest = None
    for price in prices:
        if price.date <= sim_date:
            latest = price
        else:
            break
    return latest


def _generate_signals_from_cache(run: SimulationRun, sim_date, replay_data: dict, include_details: bool = True) -> list[dict]:
    signals = []
    sector_scores = replay_data.get('sector_scores', {})

    for stock in replay_data.get('stocks', []):
        row_idx = replay_data.get('row_index_by_stock', {}).get(stock.id, {}).get(sim_date)
        rows = replay_data.get('rows_by_stock', {}).get(stock.id)
        if row_idx is None or not rows:
            continue

        try:
            row = rows[row_idx]
            params = replay_data.get('params_by_stock', {}).get(stock.id, _default_params())
            sector_score = sector_scores.get(stock.sector, 50.0)
            analyst_score = 50.0
            news_score = 50.0
            latest_price = _get_cached_price(stock.id, sim_date, replay_data)
            if not latest_price:
                continue

            technical_score = _compute_score_fast(row, params, 50.0, sector_score)
            score = _compute_score_fast(row, params, analyst_score, sector_score)

            strategy_mode = replay_data.get('strategy_mode', 'score')
            strategy_params = replay_data.get('strategy_params', {})
            buy_threshold = strategy_params.get('buy_threshold', 65)
            sell_threshold = strategy_params.get('sell_threshold', 35)

            if strategy_mode == 'trend_quality':
                ema_fast = _safe_float(row.get('ema_fast'))
                ema_slow = _safe_float(row.get('ema_slow'))
                macd_value = _safe_float(row.get('macd'))
                macd_signal_value = _safe_float(row.get('macd_signal'))
                rsi_value = _safe_float(row.get('rsi'))

                price_ok = (not strategy_params.get('require_price_above_ema_fast')) or (
                    ema_fast is not None and float(latest_price.close) >= ema_fast * 0.995
                )
                ema_ok = (not strategy_params.get('require_ema_fast_above_slow')) or (
                    ema_fast is not None and ema_slow is not None and ema_fast >= ema_slow * 0.995
                )
                macd_ok = (not strategy_params.get('require_macd_above_signal')) or (
                    macd_value is not None and macd_signal_value is not None and macd_value >= macd_signal_value
                )
                rsi_ok = rsi_value is not None and strategy_params.get('min_rsi', 0) <= rsi_value <= strategy_params.get('max_rsi', 100)
                sector_ok = sector_score >= strategy_params.get('min_sector_score', 0)

                quality_score = score
                if price_ok:
                    quality_score += 4
                if ema_ok:
                    quality_score += 5
                if macd_ok:
                    quality_score += 4
                if sector_ok:
                    quality_score += 3
                if rsi_ok:
                    quality_score += 4

                if quality_score >= buy_threshold and price_ok and ema_ok and macd_ok and sector_ok:
                    action = 'BUY'
                elif score <= sell_threshold:
                    action = 'SELL'
                else:
                    action = 'HOLD'
            else:
                if score >= buy_threshold:
                    action = 'BUY'
                elif score <= sell_threshold:
                    action = 'SELL'
                else:
                    action = 'HOLD'

            signal = {
                'stock_id': stock.id,
                'symbol': stock.symbol,
                'name': stock.name,
                'sector': stock.sector,
                'currency': stock.currency,
                'score': score,
                'action': action,
                'current_price': float(latest_price.close),
                'current_price_eur': float(latest_price.close_eur or latest_price.close),
                'atr': float(row['atr']) if not pd.isna(row['atr']) else None,
                'rsi': float(row['rsi']) if not pd.isna(row['rsi']) else None,
                'macd': float(row['macd']) if not pd.isna(row['macd']) else None,
                'ema_fast': float(row['ema_fast']) if not pd.isna(row['ema_fast']) else None,
                'ema_slow': float(row['ema_slow']) if not pd.isna(row['ema_slow']) else None,
                'technical_score': technical_score,
                'analyst_score': analyst_score,
                'news_score': news_score,
                'sector_score': sector_score,
                'risk_score': None,
                'params': params,
                'risk_json': {},
                'data_snapshot_json': {
                    'as_of_date': sim_date.isoformat(),
                    'history_points': row_idx + 1,
                },
            }
            if include_details:
                signal['reason'] = _build_reason(row, params, score, analyst_score, sector_score)
                signal['reason_json'] = {
                    'technical': {
                        'rsi': float(row['rsi']) if not pd.isna(row['rsi']) else None,
                        'macd': float(row['macd']) if not pd.isna(row['macd']) else None,
                        'ema_fast': float(row['ema_fast']) if not pd.isna(row['ema_fast']) else None,
                        'ema_slow': float(row['ema_slow']) if not pd.isna(row['ema_slow']) else None,
                    },
                    'scores': {
                        'final': score,
                        'technical': technical_score,
                        'analyst': analyst_score,
                        'news': news_score,
                        'sector': sector_score,
                    }
                }
            else:
                signal['reason'] = action
                signal['reason_json'] = {}
            signals.append(signal)
        except Exception as e:
            log.error('Cached Signal-Berechnung für %s fehlgeschlagen: %s', stock.symbol, e)

    signals.sort(key=lambda s: s['score'], reverse=True)
    return signals


def _parse_date(value):
    if hasattr(value, 'year'):
        return value
    return datetime.strptime(value, '%Y-%m-%d').date()
