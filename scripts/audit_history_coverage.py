#!/usr/bin/env python3
"""Prüft die historische Datenabdeckung pro Aktie und pro Universum.

Beispiele:
  python scripts/audit_history_coverage.py
  python scripts/audit_history_coverage.py --universe global_core
  python scripts/audit_history_coverage.py --csv out/history_coverage.csv
"""

from __future__ import annotations

import argparse
import csv
import math
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / '.env')

from app import create_app
from models import db, Stock, Price
from services.universe_store import list_universes


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Audit der Historienabdeckung pro Aktie und Universum')
    parser.add_argument('--universe', action='append', default=[], help='Nur bestimmte Universe-ID(s) prüfen')
    parser.add_argument('--csv', help='Optionaler CSV-Ausgabepfad für Symbolzeilen')
    return parser.parse_args()


def safe_int(value):
    try:
        return int(value)
    except Exception:
        return 0


def years_between(start_date, end_date):
    if not start_date or not end_date:
        return 0.0
    return max((end_date - start_date).days, 0) / 365.25


def build_symbol_rows():
    rows = []

    coverage_query = (
        db.session.query(
            Stock.symbol,
            Stock.name,
            Stock.region,
            Stock.sector,
            Stock.currency,
            db.func.min(Price.date).label('first_date'),
            db.func.max(Price.date).label('last_date'),
            db.func.count(Price.id).label('price_rows'),
        )
        .join(Price, Price.stock_id == Stock.id)
        .group_by(Stock.id)
        .order_by(Stock.symbol.asc())
    )

    today = date.today()
    for row in coverage_query.all():
        span_years = years_between(row.first_date, row.last_date)
        age_days = (today - row.last_date).days if row.last_date else None
        rows.append({
            'symbol': row.symbol,
            'name': row.name,
            'region': row.region,
            'sector': row.sector,
            'currency': row.currency,
            'first_date': row.first_date.isoformat() if row.first_date else None,
            'last_date': row.last_date.isoformat() if row.last_date else None,
            'price_rows': safe_int(row.price_rows),
            'span_years': span_years,
            'has_3y': span_years >= 3.0,
            'has_5y': span_years >= 5.0,
            'has_10y': span_years >= 10.0,
            'stale_days': age_days,
            'is_stale_30d': (age_days is not None and age_days > 30),
        })

    return rows


def print_symbol_summary(rows):
    total = len(rows)
    cov3 = sum(1 for r in rows if r['has_3y'])
    cov5 = sum(1 for r in rows if r['has_5y'])
    cov10 = sum(1 for r in rows if r['has_10y'])
    stale = sum(1 for r in rows if r['is_stale_30d'])

    print('=== Gesamtüberblick ===')
    print(f'Aktien mit Kursdaten: {total}')
    print(f'>= 3 Jahre Historie: {cov3}')
    print(f'>= 5 Jahre Historie: {cov5}')
    print(f'>= 10 Jahre Historie: {cov10}')
    print(f'Stand älter als 30 Tage: {stale}')

    shortest = sorted(rows, key=lambda r: (r['span_years'], r['price_rows']))[:10]
    print('\n=== Kürzeste Historien (Top 10) ===')
    for r in shortest:
        print(
            f"- {r['symbol']}: {r['span_years']:.2f} Jahre | "
            f"{r['first_date']} → {r['last_date']} | rows={r['price_rows']}"
        )


def build_universe_summary(rows, requested_universes=None):
    symbol_map = {row['symbol']: row for row in rows}
    universe_data = list_universes()
    universes = universe_data.get('universes', [])

    if requested_universes:
        wanted = set(requested_universes)
        universes = [u for u in universes if u.get('id') in wanted]

    summaries = []
    for universe in universes:
        symbols = universe.get('symbols') or []
        found = [symbol_map[s] for s in symbols if s in symbol_map]
        missing_symbols = [s for s in symbols if s not in symbol_map]

        under_10y_rows = sorted(
            [r for r in found if not r['has_10y']],
            key=lambda r: (r['span_years'], r['symbol'])
        )

        summaries.append({
            'id': universe.get('id'),
            'name': universe.get('name'),
            'symbols_total': len(symbols),
            'symbols_in_db': len(found),
            'symbols_missing_in_db': len(missing_symbols),
            'has_3y': sum(1 for r in found if r['has_3y']),
            'has_5y': sum(1 for r in found if r['has_5y']),
            'has_10y': sum(1 for r in found if r['has_10y']),
            'stale_30d': sum(1 for r in found if r['is_stale_30d']),
            'avg_span_years': (sum(r['span_years'] for r in found) / len(found)) if found else 0.0,
            'missing_symbols': missing_symbols,
            'under_10y_rows': under_10y_rows,
        })
    return summaries


def print_universe_summary(summaries):
    print('\n=== Universen ===')
    for s in summaries:
        print(
            f"- {s['id']} ({s['name']}): total={s['symbols_total']}, in_db={s['symbols_in_db']}, "
            f">=3J={s['has_3y']}, >=5J={s['has_5y']}, >=10J={s['has_10y']}, "
            f"stale30d={s['stale_30d']}, avg_span={s['avg_span_years']:.2f}J"
        )
        if s['missing_symbols']:
            print(f"  fehlend in DB: {', '.join(s['missing_symbols'])}")
        under_10y = s.get('under_10y_rows') or []
        if under_10y:
            print('  <10 Jahre Historie:')
            for row in under_10y:
                print(
                    f"    - {row['symbol']}: {row['span_years']:.2f}J | "
                    f"{row['first_date']} → {row['last_date']} | rows={row['price_rows']}"
                )


def write_csv(rows, path):
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        'symbol', 'name', 'region', 'sector', 'currency',
        'first_date', 'last_date', 'price_rows', 'span_years',
        'has_3y', 'has_5y', 'has_10y', 'stale_days', 'is_stale_30d'
    ]
    with out_path.open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f'\nCSV geschrieben: {out_path}')


def main():
    args = parse_args()
    app = create_app()
    with app.app_context():
        rows = build_symbol_rows()
        print_symbol_summary(rows)
        summaries = build_universe_summary(rows, requested_universes=args.universe or None)
        print_universe_summary(summaries)
        if args.csv:
            write_csv(rows, args.csv)


if __name__ == '__main__':
    main()
