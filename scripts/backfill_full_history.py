#!/usr/bin/env python3
"""Lädt lange Kurshistorie für alle DB-Aktien nach (für Replay-Tests auf alten Zeitfenstern).

Beispiel:
  python3 scripts/backfill_full_history.py --days 4600
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / '.env')

from run_scenario import create_cli_app  # noqa: E402
from services.data_fetcher import store_prices_to_db  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description='Volle Kurshistorie für alle DB-Aktien nachladen')
    parser.add_argument('--days', type=int, default=4600, help='Kalendertage zurück (Default ~12,5 Jahre)')
    args = parser.parse_args()

    app = create_cli_app()
    with app.app_context():
        from models import Stock
        universe = [{'symbol': s.symbol, 'name': s.name or s.symbol,
                     'sector': s.sector or 'Unbekannt', 'region': s.region or 'US',
                     'currency': s.currency or 'USD'}
                    for s in Stock.query.filter_by(active=True).all()]
    print(f'Backfill für {len(universe)} Aktien, {args.days} Tage...', flush=True)
    store_prices_to_db(app, universe, days=args.days)
    print('Backfill fertig.', flush=True)


if __name__ == '__main__':
    main()
