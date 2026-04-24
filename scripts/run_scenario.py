#!/usr/bin/env python3
"""Führt ein gespeichertes Szenario direkt aus.

Beispiele:
  python3 scripts/run_scenario.py --scenario cash_recycling_fast_batch
  python3 scripts/run_scenario.py --scenario cash_recycling_fast_batch --persist-chunk-days 4000 --decision-log-mode minimal
"""

from __future__ import annotations

import argparse
import json
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
from services.scenario_store import get_scenario
from services.strategy_store import list_strategies



def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Ein gespeichertes Szenario direkt ausführen')
    parser.add_argument('--scenario', help='Scenario-ID aus data/scenarios.json')
    parser.add_argument('--config', help='Pfad zu einer JSON-Datei mit Szenario-/Run-Konfiguration')
    parser.add_argument('--name', help='Optionaler Run-Name')
    parser.add_argument('--start-date', help='Override für start_date (YYYY-MM-DD)')
    parser.add_argument('--end-date', help='Override für end_date (YYYY-MM-DD)')
    parser.add_argument('--capital', type=float, help='Override für initial_capital_eur')
    parser.add_argument('--strategy', help='Override für strategy_id')
    parser.add_argument('--universe', help='Override für universe_name')
    parser.add_argument('--persist-chunk-days', type=int, help='Override für persist_chunk_days')
    parser.add_argument('--cancel-check-interval-days', type=int, help='Override für cancel_check_interval_days')
    parser.add_argument('--decision-log-mode', choices=['normal', 'debug', 'minimal'], help='Override für decision_log_mode')
    parser.add_argument('--param', action='append', default=[], help='Freier Param-Override als key=value (mehrfach möglich)')
    parser.add_argument('--dry-run', action='store_true', help='Nur Payload anzeigen, nichts ausführen')
    args = parser.parse_args()
    if not args.scenario and not args.config:
        parser.error('Bitte --scenario oder --config angeben')
    return args



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



def _parse_param_value(raw: str):
    lowered = raw.strip().lower()
    if lowered == 'true':
        return True
    if lowered == 'false':
        return False
    if lowered == 'null':
        return None
    try:
        if '.' in raw:
            return float(raw)
        return int(raw)
    except ValueError:
        return raw



def _load_config_file(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding='utf-8'))



def _load_strategies():
    data = list_strategies()
    return data.get('strategies', []) if isinstance(data, dict) else []



def _resolve_strategy_id(value: str | None) -> str | None:
    if not value:
        return value
    strategies = _load_strategies()
    normalized = value.strip().lower()

    for strategy in strategies:
        if str(strategy.get('id') or '').strip().lower() == normalized:
            return strategy.get('id')

    for strategy in strategies:
        if str(strategy.get('name') or '').strip().lower() == normalized:
            return strategy.get('id')

    return value



def _resolve_strategy_name(strategy_id: str | None) -> str | None:
    if not strategy_id:
        return strategy_id
    strategies = _load_strategies()
    normalized = strategy_id.strip().lower()
    for strategy in strategies:
        if str(strategy.get('id') or '').strip().lower() == normalized:
            return strategy.get('name') or strategy.get('id')
    return strategy_id



def build_payload(args: argparse.Namespace) -> dict:
    base = {}
    if args.config:
        base = _load_config_file(args.config)
    elif args.scenario:
        scenario = get_scenario(args.scenario)
        if not scenario:
            raise ValueError(f'Szenario {args.scenario} nicht gefunden')
        base = scenario

    params_override = dict(base.get('params_override') or base.get('strategy_params_override') or {})
    if args.persist_chunk_days is not None:
        params_override['persist_chunk_days'] = args.persist_chunk_days
    if args.cancel_check_interval_days is not None:
        params_override['cancel_check_interval_days'] = args.cancel_check_interval_days
    if args.decision_log_mode:
        params_override['decision_log_mode'] = args.decision_log_mode

    for item in args.param:
        if '=' not in item:
            raise ValueError(f'Ungültiger --param Wert: {item}. Erwartet key=value')
        key, raw_value = item.split('=', 1)
        key = key.strip()
        if not key:
            raise ValueError(f'Ungültiger --param Schlüssel: {item}')
        params_override[key] = _parse_param_value(raw_value.strip())

    strategy_id = _resolve_strategy_id(args.strategy or base.get('strategy_id'))
    strategy_name = _resolve_strategy_name(strategy_id)
    scenario_name = base.get('name') or base.get('id') or args.scenario or 'Scenario Run'
    run_name = args.name or f'{scenario_name} — {strategy_name}'

    return {
        'name': run_name,
        'start_date': args.start_date or base.get('start_date'),
        'end_date': args.end_date or base.get('end_date'),
        'initial_capital_eur': args.capital if args.capital is not None else base.get('initial_capital_eur', 10000),
        'strategy_id': strategy_id,
        'universe_name': args.universe or base.get('universe_name'),
        'notes': base.get('notes'),
        'auto_start': False,
        'strategy_params_override': params_override,
    }



def main():
    args = parse_args()
    payload = build_payload(args)

    if args.dry_run:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return

    app = create_cli_app()
    with app.app_context():
        run = create_simulation_run(payload)
        print(f'Run angelegt: id={run.id} scenario={args.scenario} strategy={run.strategy_name} universe={run.universe_name}')
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
