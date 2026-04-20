#!/usr/bin/env python3
"""Vergleicht mehrere Simulations-Runs/Strategien und erzeugt ein kompaktes Ranking.

Beispiele:
  python scripts/compare_strategy_runs.py --latest-per-strategy
  python scripts/compare_strategy_runs.py --strategy trend_quality_aggressive_v1 --strategy score_swing_v1
  python scripts/compare_strategy_runs.py --run-id 22 --run-id 23 --run-id 24
  python scripts/compare_strategy_runs.py --latest-per-strategy --csv out/strategy_compare.csv
"""

from __future__ import annotations

import argparse
import csv
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / '.env')

from app import create_app
from models import SimulationRun, SimulationTrade, SimulationDailySnapshot


PCT_FIELDS = {
    'total_return_pct',
    'benchmark_return_pct',
    'max_drawdown_pct',
    'win_rate',
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Vergleiche mehrere Strategie-Runs.')
    parser.add_argument('--run-id', type=int, action='append', default=[], help='Explizite Run-ID (mehrfach nutzbar)')
    parser.add_argument('--strategy', action='append', default=[], help='Strategie-ID; nimmt jeweils den neuesten Run')
    parser.add_argument('--latest-per-strategy', action='store_true', help='Nimmt den neuesten Run je Strategie')
    parser.add_argument('--status', default='completed', help='Statusfilter für auto-ausgewählte Runs, Standard: completed')
    parser.add_argument('--limit', type=int, default=50, help='Maximalzahl auto-ausgewählter Runs')
    parser.add_argument('--sort-by', default='total_return_pct', help='Sortierfeld, z.B. total_return_pct, sharpe_ratio, max_drawdown_pct')
    parser.add_argument('--ascending', action='store_true', help='Aufsteigend sortieren statt absteigend')
    parser.add_argument('--csv', help='Optionaler CSV-Ausgabepfad')
    return parser.parse_args()


def latest_run_per_strategy(status: str | None, limit: int):
    query = SimulationRun.query
    if status:
        query = query.filter_by(status=status)
    runs = query.order_by(SimulationRun.strategy_name.asc(), SimulationRun.id.desc()).all()

    selected = {}
    for run in runs:
        if run.strategy_name not in selected:
            selected[run.strategy_name] = run
        if len(selected) >= limit:
            break
    return list(selected.values())


def resolve_runs(args: argparse.Namespace):
    runs = []
    seen_ids = set()

    for run_id in args.run_id:
        run = SimulationRun.query.get(run_id)
        if not run:
            raise SystemExit(f'Run id={run_id} nicht gefunden')
        if run.id not in seen_ids:
            runs.append(run)
            seen_ids.add(run.id)

    for strategy_name in args.strategy:
        query = SimulationRun.query.filter_by(strategy_name=strategy_name)
        if args.status:
            query = query.filter_by(status=args.status)
        run = query.order_by(SimulationRun.id.desc()).first()
        if not run:
            raise SystemExit(f'Kein Run für Strategie {strategy_name!r} gefunden')
        if run.id not in seen_ids:
            runs.append(run)
            seen_ids.add(run.id)

    if args.latest_per_strategy:
        for run in latest_run_per_strategy(args.status, args.limit):
            if run.id not in seen_ids:
                runs.append(run)
                seen_ids.add(run.id)

    if not runs:
        raise SystemExit('Keine Runs ausgewählt. Nutze --run-id, --strategy oder --latest-per-strategy')

    return runs


def safe_float(value, default=0.0):
    if value is None:
        return default
    try:
        if isinstance(value, float) and math.isnan(value):
            return default
        return float(value)
    except Exception:
        return default


def load_snapshots(run_id: int):
    return (SimulationDailySnapshot.query
            .filter_by(run_id=run_id)
            .order_by(SimulationDailySnapshot.sim_date.asc())
            .all())


def load_trades(run_id: int):
    return (SimulationTrade.query
            .filter_by(run_id=run_id)
            .order_by(SimulationTrade.sim_date.asc(), SimulationTrade.id.asc())
            .all())


def calc_snapshot_metrics(snaps):
    if not snaps:
        return {
            'days': 0,
            'start_equity': 0.0,
            'end_equity': 0.0,
            'cagr_pct': 0.0,
        }

    start_equity = safe_float(snaps[0].equity_eur)
    end_equity = safe_float(snaps[-1].equity_eur)
    days = max((snaps[-1].sim_date - snaps[0].sim_date).days, 0) if snaps[-1].sim_date and snaps[0].sim_date else 0

    cagr_pct = 0.0
    if start_equity > 0 and end_equity > 0 and days > 0:
        years = days / 365.25
        if years > 0:
            cagr_pct = ((end_equity / start_equity) ** (1 / years) - 1) * 100

    return {
        'days': days,
        'start_equity': start_equity,
        'end_equity': end_equity,
        'cagr_pct': cagr_pct,
    }


def calc_trade_metrics(trades):
    buys = sum(1 for t in trades if t.action == 'BUY')
    sells = sum(1 for t in trades if t.action == 'SELL')
    realized_pnl = sum(safe_float(t.pnl_eur) for t in trades if t.action == 'SELL')
    avg_sell_pnl = realized_pnl / sells if sells else 0.0
    return {
        'buy_count': buys,
        'sell_count': sells,
        'realized_pnl_eur': realized_pnl,
        'avg_sell_pnl_eur': avg_sell_pnl,
    }


def collect_row(run: SimulationRun):
    snaps = load_snapshots(run.id)
    trades = load_trades(run.id)
    snap_metrics = calc_snapshot_metrics(snaps)
    trade_metrics = calc_trade_metrics(trades)

    return {
        'run_id': run.id,
        'name': run.name,
        'strategy_name': run.strategy_name,
        'status': run.status,
        'start_date': run.start_date.isoformat() if run.start_date else None,
        'end_date': run.end_date.isoformat() if run.end_date else None,
        'days': snap_metrics['days'],
        'initial_capital_eur': safe_float(run.initial_capital_eur),
        'final_equity_eur': safe_float(run.final_equity_eur),
        'total_return_pct': safe_float(run.total_return_pct),
        'benchmark_return_pct': safe_float(run.benchmark_return_pct),
        'alpha_pct': safe_float(run.total_return_pct) - safe_float(run.benchmark_return_pct),
        'max_drawdown_pct': safe_float(run.max_drawdown_pct),
        'sharpe_ratio': safe_float(run.sharpe_ratio),
        'win_rate': safe_float(run.win_rate),
        'profit_factor': safe_float(run.profit_factor),
        'total_trades': int(run.total_trades or 0),
        'winning_trades': int(run.winning_trades or 0),
        'losing_trades': int(run.losing_trades or 0),
        'cagr_pct': snap_metrics['cagr_pct'],
        'buy_count': trade_metrics['buy_count'],
        'sell_count': trade_metrics['sell_count'],
        'realized_pnl_eur': trade_metrics['realized_pnl_eur'],
        'avg_sell_pnl_eur': trade_metrics['avg_sell_pnl_eur'],
    }


def format_value(key, value):
    if value is None:
        return '-'
    if isinstance(value, float):
        if key in PCT_FIELDS or key in {'alpha_pct', 'cagr_pct'}:
            return f'{value:.2f}%'
        if key in {'final_equity_eur', 'initial_capital_eur', 'realized_pnl_eur', 'avg_sell_pnl_eur'}:
            return f'{value:.2f}'
        return f'{value:.4f}'
    return str(value)


def print_table(rows, sort_by: str, ascending: bool):
    if not rows:
        print('Keine Daten.')
        return

    if sort_by not in rows[0]:
        raise SystemExit(f'Unbekanntes Sortierfeld: {sort_by}')

    rows = sorted(rows, key=lambda r: (r.get(sort_by) is None, r.get(sort_by, 0)), reverse=not ascending)

    columns = [
        'run_id', 'strategy_name', 'status', 'days',
        'total_return_pct', 'alpha_pct', 'cagr_pct',
        'max_drawdown_pct', 'sharpe_ratio', 'win_rate', 'profit_factor',
        'total_trades', 'final_equity_eur'
    ]

    widths = {}
    for col in columns:
        widths[col] = max(len(col), *(len(format_value(col, row.get(col))) for row in rows))

    header = ' | '.join(col.ljust(widths[col]) for col in columns)
    sep = '-+-'.join('-' * widths[col] for col in columns)
    print(header)
    print(sep)
    for row in rows:
        print(' | '.join(format_value(col, row.get(col)).ljust(widths[col]) for col in columns))

    print('\nTop-Interpretation:')
    best = rows[0]
    print(
        f"- Führend nach {sort_by}: Run {best['run_id']} / {best['strategy_name']} "
        f"mit Return {best['total_return_pct']:.2f}% bei Drawdown {best['max_drawdown_pct']:.2f}%"
    )


def write_csv(rows, path: str):
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0].keys())
    with out_path.open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f'\nCSV geschrieben: {out_path}')


def main():
    args = parse_args()
    app = create_app()
    with app.app_context():
        runs = resolve_runs(args)
        rows = [collect_row(run) for run in runs]
        print_table(rows, sort_by=args.sort_by, ascending=args.ascending)
        if args.csv:
            write_csv(rows, args.csv)


if __name__ == '__main__':
    main()
