#!/usr/bin/env python3
"""Führt genau einen Simulationslauf aus, ohne die volle Web-App zu starten.

Beispiel:
  python scripts/run_simulation.py \
    --strategy cash_recycling_v1 \
    --universe global_core_10y \
    --start 2016-01-01 \
    --end 2026-04-20 \
    --name "Cash Recycling 10Y Global Core"
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
from flask import Flask
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

load_dotenv(ROOT / '.env')

import config
from models import db, Account
from services.replay_engine import create_simulation_run, run_historical_replay


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Einen historischen Simulationslauf direkt ausführen')
    parser.add_argument('--strategy', required=True, help='Strategie-ID, z.B. cash_recycling_v1')
    parser.add_argument('--universe', required=True, help='Universe-ID, z.B. global_core_10y')
    parser.add_argument('--start', required=True, help='Startdatum YYYY-MM-DD')
    parser.add_argument('--end', required=True, help='Enddatum YYYY-MM-DD')
    parser.add_argument('--name', help='Optionaler Run-Name')
    parser.add_argument('--capital', type=float, default=10000.0, help='Startkapital in EUR')
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


def main():
    args = parse_args()
    app = create_cli_app()
    payload = {
        'name': args.name or f'{args.strategy} {args.start}..{args.end}',
        'start_date': args.start,
        'end_date': args.end,
        'initial_capital_eur': args.capital,
        'strategy_id': args.strategy,
        'universe_name': args.universe,
        'auto_start': False,
    }

    with app.app_context():
        run = create_simulation_run(payload)
        print(f'Run angelegt: id={run.id} strategy={run.strategy_name} universe={run.universe_name}')
        run_historical_replay(app, run.id)
        db.session.refresh(run)
        print(
            'Fertig: '
            f'status={run.status} '
            f'final_equity_eur={run.final_equity_eur} '
            f'total_return_pct={run.total_return_pct} '
            f'max_drawdown_pct={run.max_drawdown_pct} '
            f'sharpe_ratio={run.sharpe_ratio}'
        )


if __name__ == '__main__':
    main()
