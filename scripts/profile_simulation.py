#!/usr/bin/env python3
"""Profiling-Tool für historische Simulationen.

Beispiele:
  python3 scripts/profile_simulation.py --run-id 42
  python3 scripts/profile_simulation.py \
    --strategy cash_recycling_v1 \
    --universe global_core_10y \
    --start 2016-01-01 \
    --end 2026-04-20 \
    --name "Profile Run" \
    --capital 10000
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
from flask import Flask
from sqlalchemy import event, func, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

load_dotenv(ROOT / '.env')

import config
from models import (
    db,
    Account,
    SimulationRun,
    SimulationTrade,
    SimulationDailySnapshot,
    DecisionLog,
    SimulationPosition,
)
from services import replay_engine as replay
from services.replay_engine import create_simulation_run


class SqlProfiler:
    def __init__(self):
        self.reset()

    def reset(self):
        self.statement_count = 0
        self.total_sql_time = 0.0
        self.by_verb = Counter()
        self.slowest = []

    def before(self, conn, cursor, statement, parameters, context, executemany):
        context._query_start_time = time.perf_counter()

    def after(self, conn, cursor, statement, parameters, context, executemany):
        elapsed = time.perf_counter() - getattr(context, '_query_start_time', time.perf_counter())
        self.statement_count += 1
        self.total_sql_time += elapsed
        verb = (statement or '').strip().split(None, 1)[0].upper() if statement else 'UNKNOWN'
        self.by_verb[verb] += 1
        item = {
            'elapsed_ms': round(elapsed * 1000, 3),
            'verb': verb,
            'statement': ' '.join((statement or '').split())[:500],
        }
        self.slowest.append(item)
        self.slowest.sort(key=lambda x: x['elapsed_ms'], reverse=True)
        if len(self.slowest) > 15:
            self.slowest = self.slowest[:15]


class ReplayMetrics:
    def __init__(self):
        self.reset()

    def reset(self):
        self.phase_seconds = Counter()
        self.counters = Counter()
        self.flush_sizes = []
        self.started_at = None
        self.finished_at = None

    @contextmanager
    def phase(self, name: str):
        start = time.perf_counter()
        try:
            yield
        finally:
            self.phase_seconds[name] += time.perf_counter() - start

    def mark_flush(self, decisions: int, trades: int, snapshots: int):
        self.counters['flush_count'] += 1
        self.flush_sizes.append({
            'decisions': decisions,
            'trades': trades,
            'snapshots': snapshots,
        })
        self.counters['persisted_decisions'] += decisions
        self.counters['persisted_trades'] += trades
        self.counters['persisted_snapshots'] += snapshots


SQL_PROFILER = SqlProfiler()
REPLAY_METRICS = ReplayMetrics()
_ORIGINALS = {}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Profiling-Tool für historische Simulationen')
    parser.add_argument('--run-id', type=int, help='Bestehenden Simulationslauf erneut/profilierend ausführen')
    parser.add_argument('--strategy', help='Strategie-ID')
    parser.add_argument('--universe', help='Universe-ID')
    parser.add_argument('--start', help='Startdatum YYYY-MM-DD')
    parser.add_argument('--end', help='Enddatum YYYY-MM-DD')
    parser.add_argument('--name', help='Optionaler Run-Name')
    parser.add_argument('--capital', type=float, default=10000.0, help='Startkapital in EUR')
    parser.add_argument('--output', help='Pfad für JSON-Ausgabe')
    return parser.parse_args()


def create_cli_app() -> Flask:
    app = Flask(__name__)
    app.config['SECRET_KEY'] = config.SECRET_KEY
    app.config['SQLALCHEMY_DATABASE_URI'] = config.SQLALCHEMY_DATABASE_URI
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
        'connect_args': {
            'connect_timeout': 10,
        },
        'pool_pre_ping': True,
    }

    db.init_app(app)

    with app.app_context():
        db.create_all()
        _init_account()
        _init_performance_indexes()

    return app


def _init_account():
    if not Account.query.first():
        account = Account(
            cash_eur=config.STARTING_CAPITAL,
            equity_eur=config.STARTING_CAPITAL,
        )
        db.session.add(account)
        db.session.commit()


def _init_performance_indexes():
    index_statements = [
        'CREATE INDEX IF NOT EXISTS idx_prices_stock_date_desc ON prices (stock_id, date DESC)',
        'CREATE INDEX IF NOT EXISTS idx_decision_logs_run_date_id ON decision_logs (run_id, sim_date DESC, id DESC)',
        'CREATE INDEX IF NOT EXISTS idx_decision_logs_run_executed ON decision_logs (run_id, executed)',
        'CREATE INDEX IF NOT EXISTS idx_simulation_trades_run_date_id ON simulation_trades (run_id, sim_date DESC, id DESC)',
        'CREATE INDEX IF NOT EXISTS idx_simulation_positions_run_stock ON simulation_positions (run_id, stock_id)',
        'CREATE INDEX IF NOT EXISTS idx_simulation_daily_snapshots_run_date_desc ON simulation_daily_snapshots (run_id, sim_date DESC)',
    ]
    try:
        for stmt in index_statements:
            db.session.execute(text(stmt))
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()


def install_sql_profiler(engine: Engine):
    event.listen(engine, 'before_cursor_execute', SQL_PROFILER.before)
    event.listen(engine, 'after_cursor_execute', SQL_PROFILER.after)


def install_replay_hooks():
    if _ORIGINALS:
        return

    _ORIGINALS['build_replay_data_cache'] = replay._build_replay_data_cache
    _ORIGINALS['generate_signals_from_cache'] = replay._generate_signals_from_cache
    _ORIGINALS['update_open_positions_in_memory'] = replay._update_open_positions_in_memory
    _ORIGINALS['persist_replay_buffers'] = replay._persist_replay_buffers
    _ORIGINALS['replace_open_positions'] = replay._replace_open_positions

    def wrapped_build_replay_data_cache(run):
        with REPLAY_METRICS.phase('build_replay_data_cache'):
            return _ORIGINALS['build_replay_data_cache'](run)

    def wrapped_generate_signals_from_cache(run, sim_date, replay_data, include_details=True):
        with REPLAY_METRICS.phase('generate_signals'):
            signals = _ORIGINALS['generate_signals_from_cache'](run, sim_date, replay_data, include_details=include_details)
        REPLAY_METRICS.counters['signal_days'] += 1
        REPLAY_METRICS.counters['signals_total'] += len(signals)
        return signals

    def wrapped_update_open_positions_in_memory(run, sim_date, replay_data=None, open_positions=None, trade_buffer=None):
        with REPLAY_METRICS.phase('update_open_positions'):
            result = _ORIGINALS['update_open_positions_in_memory'](run, sim_date, replay_data, open_positions, trade_buffer)
        REPLAY_METRICS.counters['position_update_days'] += 1
        REPLAY_METRICS.counters['open_positions_seen'] += len(open_positions or {})
        return result

    def wrapped_persist_replay_buffers(decision_log_buffer, trade_buffer, snapshot_buffer):
        decisions = len(decision_log_buffer)
        trades = len(trade_buffer)
        snapshots = len(snapshot_buffer)
        with REPLAY_METRICS.phase('persist_replay_buffers'):
            result = _ORIGINALS['persist_replay_buffers'](decision_log_buffer, trade_buffer, snapshot_buffer)
        REPLAY_METRICS.mark_flush(decisions, trades, snapshots)
        return result

    def wrapped_replace_open_positions(run_id, open_positions):
        with REPLAY_METRICS.phase('replace_open_positions'):
            return _ORIGINALS['replace_open_positions'](run_id, open_positions)

    replay._build_replay_data_cache = wrapped_build_replay_data_cache
    replay._generate_signals_from_cache = wrapped_generate_signals_from_cache
    replay._update_open_positions_in_memory = wrapped_update_open_positions_in_memory
    replay._persist_replay_buffers = wrapped_persist_replay_buffers
    replay._replace_open_positions = wrapped_replace_open_positions


def resolve_payload(args: argparse.Namespace) -> dict:
    if args.run_id:
        existing = db.session.get(SimulationRun, args.run_id)
        if not existing:
            raise ValueError(f'Run {args.run_id} nicht gefunden')
        return {
            'name': f'{existing.name} [profiled {datetime.now(timezone.utc).isoformat()}]',
            'start_date': existing.start_date.isoformat(),
            'end_date': existing.end_date.isoformat(),
            'initial_capital_eur': existing.initial_capital_eur,
            'strategy_id': existing.strategy_name,
            'strategy_version': existing.strategy_version,
            'universe_name': existing.universe_name,
            'notes': (existing.notes or '') + '\nProfiled clone run',
            'auto_start': False,
        }

    required = ['strategy', 'universe', 'start', 'end']
    missing = [field for field in required if not getattr(args, field)]
    if missing:
        raise ValueError(f'Fehlende Argumente: {", ".join(missing)}')

    return {
        'name': args.name or f'profile {args.strategy} {args.start}..{args.end}',
        'start_date': args.start,
        'end_date': args.end,
        'initial_capital_eur': args.capital,
        'strategy_id': args.strategy,
        'universe_name': args.universe,
        'auto_start': False,
    }


def collect_run_stats(run_id: int) -> dict:
    run = db.session.get(SimulationRun, run_id)
    trade_count = SimulationTrade.query.filter_by(run_id=run_id).count()
    snapshot_count = SimulationDailySnapshot.query.filter_by(run_id=run_id).count()
    decision_count = DecisionLog.query.filter_by(run_id=run_id).count()
    open_positions = SimulationPosition.query.filter_by(run_id=run_id).count()

    return {
        'run': run.to_dict() if run else None,
        'trade_count': trade_count,
        'snapshot_count': snapshot_count,
        'decision_count': decision_count,
        'open_positions_count': open_positions,
    }


def build_summary(run_id: int, total_runtime: float) -> dict:
    stats = collect_run_stats(run_id)
    run = stats['run'] or {}
    total_days = None
    if run.get('start_date') and run.get('end_date'):
        start = datetime.fromisoformat(run['start_date']).date()
        end = datetime.fromisoformat(run['end_date']).date()
        total_days = (end - start).days + 1

    flush_count = REPLAY_METRICS.counters.get('flush_count', 0)
    avg_flush_sizes = {
        'decisions': round(sum(x['decisions'] for x in REPLAY_METRICS.flush_sizes) / flush_count, 2) if flush_count else 0,
        'trades': round(sum(x['trades'] for x in REPLAY_METRICS.flush_sizes) / flush_count, 2) if flush_count else 0,
        'snapshots': round(sum(x['snapshots'] for x in REPLAY_METRICS.flush_sizes) / flush_count, 2) if flush_count else 0,
    }

    return {
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'run_id': run_id,
        'total_runtime_sec': round(total_runtime, 3),
        'days': total_days,
        'time_per_day_ms': round((total_runtime / total_days) * 1000, 3) if total_days else None,
        'time_per_trade_ms': round((total_runtime / stats['trade_count']) * 1000, 3) if stats['trade_count'] else None,
        'sql': {
            'statement_count': SQL_PROFILER.statement_count,
            'total_sql_time_sec': round(SQL_PROFILER.total_sql_time, 3),
            'by_verb': dict(SQL_PROFILER.by_verb),
            'slowest': SQL_PROFILER.slowest,
        },
        'replay_metrics': {
            'phase_seconds': {k: round(v, 3) for k, v in REPLAY_METRICS.phase_seconds.items()},
            'counters': dict(REPLAY_METRICS.counters),
            'flush_count': flush_count,
            'avg_flush_sizes': avg_flush_sizes,
            'flush_sizes_preview': REPLAY_METRICS.flush_sizes[:10],
        },
        'result': stats,
    }


def print_human_summary(summary: dict):
    print('\n=== Simulation Profiling Summary ===')
    print(f"Run ID:             {summary['run_id']}")
    print(f"Runtime:            {summary['total_runtime_sec']} s")
    if summary.get('days'):
        print(f"Days:               {summary['days']}")
        print(f"Time per day:       {summary['time_per_day_ms']} ms")
    if summary.get('time_per_trade_ms') is not None:
        print(f"Time per trade:     {summary['time_per_trade_ms']} ms")

    sql = summary['sql']
    print(f"SQL statements:     {sql['statement_count']}")
    print(f"SQL total time:     {sql['total_sql_time_sec']} s")
    print(f"SQL by verb:        {sql['by_verb']}")

    replay_metrics = summary['replay_metrics']
    print(f"Flush count:        {replay_metrics['flush_count']}")
    print(f"Avg flush sizes:    {replay_metrics['avg_flush_sizes']}")
    print(f"Phase seconds:      {replay_metrics['phase_seconds']}")

    result = summary['result']
    print(f"Trades:             {result['trade_count']}")
    print(f"Snapshots:          {result['snapshot_count']}")
    print(f"Decisions:          {result['decision_count']}")
    print(f"Open positions:     {result['open_positions_count']}")

    if sql['slowest']:
        print('\nTop slow SQL:')
        for row in sql['slowest'][:5]:
            print(f"- {row['elapsed_ms']} ms [{row['verb']}] {row['statement']}")


def main():
    args = parse_args()
    app = create_cli_app()

    with app.app_context():
        install_sql_profiler(db.engine)
        install_replay_hooks()
        SQL_PROFILER.reset()
        REPLAY_METRICS.reset()

        payload = resolve_payload(args)
        run = create_simulation_run(payload)

        started = time.perf_counter()
        replay.run_historical_replay(app, run.id)
        total_runtime = time.perf_counter() - started

        summary = build_summary(run.id, total_runtime)
        print_human_summary(summary)

        output_path = args.output
        if not output_path:
            out_dir = ROOT / 'tmp'
            out_dir.mkdir(exist_ok=True)
            output_path = out_dir / f"sim_profile_{run.id}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
        else:
            output_path = Path(output_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)

        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(summary, f, indent=2, ensure_ascii=False)

        print(f"\nJSON written to: {output_path}")


if __name__ == '__main__':
    main()
