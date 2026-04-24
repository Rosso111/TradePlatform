#!/usr/bin/env python3
"""Compare two simulation runs and show where they diverge.

Usage:
  python scripts/compare_sim_runs.py --run-a 23 --run-b 24
  python scripts/compare_sim_runs.py --name-a "Trend Quality fast2" --name-b "Trend Quality Speed"
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / '.env')

from app import create_app
from models import SimulationRun, SimulationTrade, SimulationDailySnapshot


EPS = 0.01


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Compare two simulation runs.')
    parser.add_argument('--run-a', type=int)
    parser.add_argument('--run-b', type=int)
    parser.add_argument('--name-a')
    parser.add_argument('--name-b')
    return parser.parse_args()


def resolve_run(args: argparse.Namespace, side: str):
    run_id = getattr(args, f'run_{side}')
    name = getattr(args, f'name_{side}')

    if run_id is not None:
        run = SimulationRun.query.get(run_id)
        if not run:
            raise SystemExit(f'Run {side.upper()} with id={run_id} not found')
        return run

    if name:
        runs = (SimulationRun.query
                .filter_by(name=name)
                .order_by(SimulationRun.id.desc())
                .all())
        if not runs:
            raise SystemExit(f'Run {side.upper()} with name={name!r} not found')
        if len(runs) > 1:
            print(f'Info: multiple runs named {name!r}; using latest id={runs[0].id}')
        return runs[0]

    raise SystemExit(f'Provide --run-{side} or --name-{side}')


def approx_equal(a, b, eps=EPS):
    a = 0.0 if a is None else float(a)
    b = 0.0 if b is None else float(b)
    return abs(a - b) <= eps


def summarize_run(run: SimulationRun):
    print(f'Run {run.id}: {run.name}')
    print(f'  status={run.status} strategy={run.strategy_name} range={run.start_date}..{run.end_date}')
    print(f'  final_equity={run.final_equity_eur} total_return={run.total_return_pct} trades={run.total_trades}')


def compare_metadata(run_a: SimulationRun, run_b: SimulationRun):
    fields = [
        'strategy_name', 'strategy_version', 'universe_name',
        'start_date', 'end_date', 'initial_capital_eur'
    ]
    diffs = []
    for field in fields:
        if getattr(run_a, field) != getattr(run_b, field):
            diffs.append((field, getattr(run_a, field), getattr(run_b, field)))
    return diffs


def load_trades(run_id: int):
    return (SimulationTrade.query
            .filter_by(run_id=run_id)
            .order_by(SimulationTrade.sim_date.asc(), SimulationTrade.id.asc())
            .all())


def load_snapshots(run_id: int):
    return (SimulationDailySnapshot.query
            .filter_by(run_id=run_id)
            .order_by(SimulationDailySnapshot.sim_date.asc())
            .all())


def compare_trades(trades_a, trades_b):
    print('\nTrade comparison')
    print(f'  count_a={len(trades_a)} count_b={len(trades_b)}')

    max_len = max(len(trades_a), len(trades_b))
    for idx in range(max_len):
        ta = trades_a[idx] if idx < len(trades_a) else None
        tb = trades_b[idx] if idx < len(trades_b) else None
        if ta is None or tb is None:
            print(f'  first difference at trade #{idx + 1}: one run has fewer trades')
            print(f'    A={format_trade(ta)}')
            print(f'    B={format_trade(tb)}')
            return

        comparable = (
            ta.sim_date == tb.sim_date and
            ta.action == tb.action and
            ta.stock.symbol == tb.stock.symbol and
            approx_equal(ta.shares, tb.shares, eps=0.0001) and
            approx_equal(ta.total_eur, tb.total_eur)
        )
        if not comparable:
            print(f'  first difference at trade #{idx + 1}:')
            print(f'    A={format_trade(ta)}')
            print(f'    B={format_trade(tb)}')
            return

    print('  no trade differences found')


def format_trade(trade):
    if trade is None:
        return None
    return {
        'date': trade.sim_date.isoformat() if trade.sim_date else None,
        'action': trade.action,
        'symbol': trade.stock.symbol,
        'shares': round(float(trade.shares or 0.0), 4),
        'total_eur': round(float(trade.total_eur or 0.0), 2),
        'pnl_eur': round(float(trade.pnl_eur or 0.0), 2),
        'reason': trade.reason,
    }


def compare_snapshots(snaps_a, snaps_b):
    print('\nSnapshot comparison')
    print(f'  count_a={len(snaps_a)} count_b={len(snaps_b)}')

    by_date_a = {s.sim_date: s for s in snaps_a}
    by_date_b = {s.sim_date: s for s in snaps_b}
    all_dates = sorted(set(by_date_a) | set(by_date_b))

    for sim_date in all_dates:
        sa = by_date_a.get(sim_date)
        sb = by_date_b.get(sim_date)
        if sa is None or sb is None:
            print(f'  first difference at {sim_date}: snapshot missing in one run')
            print(f'    A={format_snapshot(sa)}')
            print(f'    B={format_snapshot(sb)}')
            return

        if not (
            approx_equal(sa.equity_eur, sb.equity_eur) and
            approx_equal(sa.cash_eur, sb.cash_eur) and
            approx_equal(sa.positions_value_eur, sb.positions_value_eur) and
            int(sa.open_positions or 0) == int(sb.open_positions or 0)
        ):
            print(f'  first difference at {sim_date}:')
            print(f'    A={format_snapshot(sa)}')
            print(f'    B={format_snapshot(sb)}')
            return

    print('  no snapshot differences found')


def format_snapshot(snapshot):
    if snapshot is None:
        return None
    return {
        'date': snapshot.sim_date.isoformat() if snapshot.sim_date else None,
        'equity_eur': round(float(snapshot.equity_eur or 0.0), 2),
        'cash_eur': round(float(snapshot.cash_eur or 0.0), 2),
        'positions_value_eur': round(float(snapshot.positions_value_eur or 0.0), 2),
        'open_positions': int(snapshot.open_positions or 0),
    }


def compare_trade_counts_by_day(trades_a, trades_b):
    print('\nTrade-day summary')
    counts_a = Counter((t.sim_date, t.action) for t in trades_a)
    counts_b = Counter((t.sim_date, t.action) for t in trades_b)
    keys = sorted(set(counts_a) | set(counts_b))
    diffs = []
    for key in keys:
        if counts_a.get(key, 0) != counts_b.get(key, 0):
            diffs.append((key, counts_a.get(key, 0), counts_b.get(key, 0)))
    if not diffs:
        print('  no per-day trade count differences found')
        return
    print('  first 10 per-day trade count differences:')
    for (sim_date, action), a_count, b_count in diffs[:10]:
        print(f'    {sim_date} {action}: A={a_count} B={b_count}')


def main():
    args = parse_args()
    app = create_app()
    with app.app_context():
        run_a = resolve_run(args, 'a')
        run_b = resolve_run(args, 'b')

        print('Comparing runs')
        summarize_run(run_a)
        summarize_run(run_b)

        metadata_diffs = compare_metadata(run_a, run_b)
        print('\nMetadata differences')
        if not metadata_diffs:
            print('  none')
        else:
            for field, a, b in metadata_diffs:
                print(f'  {field}: A={a!r} B={b!r}')

        trades_a = load_trades(run_a.id)
        trades_b = load_trades(run_b.id)
        snaps_a = load_snapshots(run_a.id)
        snaps_b = load_snapshots(run_b.id)

        compare_trade_counts_by_day(trades_a, trades_b)
        compare_trades(trades_a, trades_b)
        compare_snapshots(snaps_a, snaps_b)


if __name__ == '__main__':
    main()
