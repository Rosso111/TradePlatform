"""
Simulation Routes — Historische Replay-Simulationen
"""
from flask import Blueprint, jsonify, request
from flask_login import login_required, current_user
from datetime import datetime, timezone
import logging
import threading

from models import (
    db, Stock, Price, SimulationRun, SimulationPosition, SimulationTrade,
    DecisionLog, SimulationDailySnapshot,
)
from repositories.simulation_repo import (
    delete_simulation_runs, get_simulations_for_user, get_all_simulations,
)
from services.replay_engine import _calculate_benchmark_return_until_date

log = logging.getLogger(__name__)
simulations_bp = Blueprint('simulations', __name__, url_prefix='/api')


@simulations_bp.route('/simulations', methods=['GET'])
@login_required
def get_simulations():
    # Simulations are shared analytical results — all users see all runs.
    runs = get_all_simulations()
    return jsonify([run.to_dict() for run in runs])


@simulations_bp.route('/simulations', methods=['DELETE'])
@login_required
def delete_all_simulations():
    # Implements: G-02, API-27
    if current_user.role != 'admin':
        runs = get_simulations_for_user(current_user.id)
    else:
        runs = get_all_simulations()
    active = [run for run in runs if str(run.status).upper() in ('RUNNING', 'CANCEL_REQUESTED')]
    if active:
        return jsonify({
            'success': False,
            'error': 'Laufende oder abbrechende Simulationen bitte erst fertig abbrechen lassen.'
        }), 409

    try:
        deleted_ids = [run.id for run in runs]
        delete_simulation_runs(deleted_ids)
        db.session.commit()
        return jsonify({'success': True, 'deleted_run_ids': deleted_ids, 'deleted_count': len(deleted_ids)})
    except Exception as e:
        db.session.rollback()
        log.error(f"Alle Simulationen löschen: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@simulations_bp.route('/simulations', methods=['POST'])
@login_required
def create_simulation():
    # Implements: G-02, API-27
    from flask import current_app
    from services.replay_engine import create_simulation_run, run_historical_replay

    payload = request.get_json(silent=True) or {}
    required = ['start_date', 'end_date']
    missing = [field for field in required if not payload.get(field)]
    if missing:
        return jsonify({'success': False, 'error': f"Fehlende Felder: {', '.join(missing)}"}), 400

    try:
        run = create_simulation_run(payload)
        run.user_id = current_user.id  # Implements: G-02
        db.session.flush()
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


@simulations_bp.route('/simulations/<int:run_id>', methods=['GET'])
@login_required
def get_simulation(run_id):
    # Implements: G-02, API-27
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


@simulations_bp.route('/simulations/<int:run_id>', methods=['DELETE'])
@login_required
def delete_simulation(run_id):
    # Implements: G-02, API-27
    run = SimulationRun.query.get_or_404(run_id)
    if run.user_id != current_user.id and current_user.role != 'admin':
        return jsonify({'error': 'Keine Berechtigung'}), 403

    if str(run.status).upper() in ('RUNNING', 'CANCEL_REQUESTED'):
        return jsonify({
            'success': False,
            'error': 'Laufende oder abbrechende Simulationen bitte erst fertig abbrechen lassen.'
        }), 409

    try:
        delete_simulation_runs([run_id])
        db.session.commit()
        return jsonify({'success': True, 'deleted_run_id': run_id})
    except Exception as e:
        db.session.rollback()
        log.error(f"Simulation löschen: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@simulations_bp.route('/simulations/<int:run_id>/cancel', methods=['POST'])
@login_required
def cancel_simulation(run_id):
    # Implements: G-02, API-27
    run = SimulationRun.query.get_or_404(run_id)
    if run.user_id != current_user.id and current_user.role != 'admin':
        return jsonify({'error': 'Keine Berechtigung'}), 403

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


@simulations_bp.route('/simulations/<int:run_id>/equity', methods=['GET'])
@login_required
def get_simulation_equity(run_id):
    # Implements: G-02, API-27
    run = SimulationRun.query.get_or_404(run_id)
    rows = (SimulationDailySnapshot.query
            .filter_by(run_id=run_id)
            .order_by(SimulationDailySnapshot.sim_date.asc())
            .all())
    return jsonify([row.to_dict() for row in rows])


@simulations_bp.route('/simulations/<int:run_id>/trades', methods=['GET'])
@login_required
def get_simulation_trades(run_id):
    # Implements: G-02, API-27
    run = SimulationRun.query.get_or_404(run_id)
    limit = min(int(request.args.get('limit', 300)), 1000)
    rows = (SimulationTrade.query
            .filter_by(run_id=run_id)
            .order_by(SimulationTrade.sim_date.desc(), SimulationTrade.id.desc())
            .limit(limit)
            .all())
    return jsonify([row.to_dict() for row in rows])


@simulations_bp.route('/simulations/<int:run_id>/positions', methods=['GET'])
@login_required
def get_simulation_positions(run_id):
    # Implements: G-02, API-27
    run = SimulationRun.query.get_or_404(run_id)
    rows = (SimulationPosition.query
            .filter_by(run_id=run_id)
            .order_by(SimulationPosition.opened_at_sim_date.desc(), SimulationPosition.id.desc())
            .all())
    return jsonify([row.to_dict() for row in rows])


@simulations_bp.route('/simulations/<int:run_id>/decisions', methods=['GET'])
@login_required
def get_simulation_decisions(run_id):
    # Implements: G-02, API-27
    run = SimulationRun.query.get_or_404(run_id)
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


@simulations_bp.route('/simulations/<int:run_id>/metrics', methods=['GET'])
@login_required
def get_simulation_metrics(run_id):
    # Implements: G-02, API-27
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

    import json as _json, re as _re
    _tax_summary = {}
    if run.notes:
        _m = _re.search(r'tax_summary=(\{.*\})', run.notes)
        if _m:
            try:
                _tax_summary = _json.loads(_m.group(1))
            except Exception:
                pass

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
        'kest_total': _tax_summary.get('kest_total', 0.0),
        'kest_rate_pct': _tax_summary.get('kest_rate_pct', 0.0),
        'kest_by_year': _tax_summary.get('kest_by_year', {}),
        'commission_total': _tax_summary.get('commission_total', 0.0),
        'commission_by_year': _tax_summary.get('commission_by_year', {}),
        'live': {
            'equity_eur': round(live_equity or 0.0, 2),
            'latest_snapshot_date': latest_snapshot.sim_date.isoformat() if latest_snapshot and latest_snapshot.sim_date else None,
            'open_positions': latest_snapshot.open_positions if latest_snapshot else 0,
            'positions_value_eur': round((latest_snapshot.positions_value_eur if latest_snapshot else 0.0) or 0.0, 2),
            'cash_eur': round((latest_snapshot.cash_eur if latest_snapshot else run.initial_capital_eur) or 0.0, 2),
        },
    })


@simulations_bp.route('/simulations/<int:run_id>/benchmark', methods=['GET'])
@login_required
def get_simulation_benchmark(run_id):
    # Implements: G-02, API-27
    run = SimulationRun.query.get_or_404(run_id)
    rows = (SimulationDailySnapshot.query
            .filter_by(run_id=run_id)
            .order_by(SimulationDailySnapshot.sim_date.asc())
            .all())

    if not rows:
        return jsonify({'run_id': run.id, 'benchmark_name': 'buy_and_hold_first_active_stock', 'points': []})

    first_stock = Stock.query.filter_by(active=True).order_by(Stock.symbol.asc()).first()
    if not first_stock:
        return jsonify({'run_id': run.id, 'benchmark_name': 'buy_and_hold_first_active_stock', 'points': []})

    benchmark_prices = (Price.query
                        .filter(Price.stock_id == first_stock.id, Price.date <= run.end_date)
                        .order_by(Price.date.asc())
                        .all())
    start_price = next((price for price in benchmark_prices if price.date >= run.start_date), None)
    if not start_price:
        return jsonify({'run_id': run.id, 'benchmark_name': 'buy_and_hold_first_active_stock', 'points': []})

    start_eur = start_price.close_eur or start_price.close
    if not start_eur or start_eur <= 0:
        return jsonify({'run_id': run.id, 'benchmark_name': 'buy_and_hold_first_active_stock', 'points': []})

    benchmark_points = []
    price_idx = 0
    latest_price = None
    for row in rows:
        while price_idx < len(benchmark_prices) and benchmark_prices[price_idx].date <= row.sim_date:
            latest_price = benchmark_prices[price_idx]
            price_idx += 1

        if not latest_price:
            continue

        end_eur = latest_price.close_eur or latest_price.close
        benchmark_return_pct = ((end_eur - start_eur) / start_eur * 100) if start_eur > 0 else 0.0
        benchmark_value = run.initial_capital_eur * (1 + (benchmark_return_pct / 100.0))
        benchmark_points.append({
            'sim_date': row.sim_date.isoformat(),
            'value_eur': round(benchmark_value, 2),
        })

    return jsonify({
        'run_id': run.id,
        'benchmark_name': 'buy_and_hold_first_active_stock',
        'points': benchmark_points,
    })
