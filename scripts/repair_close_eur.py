#!/usr/bin/env python3
"""Berechnet close_eur aller Preiszeilen mit historischen Wechselkursen neu.

Hintergrund: close_eur wurde bisher mit dem Wechselkurs des *Ladetages*
berechnet statt mit dem Kurs des jeweiligen Handelstages. Zusätzlich fehlte
im Backfill-Skript die GBX-Division für LSE-Symbole (.L), deren close_eur
dadurch um Faktor 100 zu hoch ist.

Dieses Skript lädt die FX-Historie über den gesamten Preiszeitraum,
speichert sie in die exchange_rates-Tabelle und rechnet close_eur für alle
Aktien datumsgenau neu.

Beispiele:
  python scripts/repair_close_eur.py --dry-run
  python scripts/repair_close_eur.py
  python scripts/repair_close_eur.py --symbols SHEL.L AAPL
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / '.env')

import config
from app import create_app
from models import db, Stock, Price
from services.data_fetcher import (
    FxLookup, close_to_eur, fetch_exchange_rates, fetch_fx_history,
    store_fx_history,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='close_eur mit historischen Wechselkursen neu berechnen')
    parser.add_argument('--dry-run', action='store_true', help='Nur anzeigen, nichts schreiben')
    parser.add_argument('--symbols', nargs='*', default=[], help='Nur diese Symbole reparieren (Standard: alle)')
    parser.add_argument('--tolerance', type=float, default=1e-6,
                        help='Relative Abweichung, ab der ein Wert als geändert gilt (Standard: 1e-6)')
    return parser.parse_args()


def main():
    args = parse_args()
    # test_config verhindert Scheduler-Start und initialen Datenload —
    # das Skript soll nur lesen/schreiben, nicht den Handelstakt starten.
    app = create_app(test_config={'SQLALCHEMY_DATABASE_URI': config.SQLALCHEMY_DATABASE_URI})

    with app.app_context():
        oldest = Price.query.order_by(Price.date.asc()).first()
        if not oldest:
            print('Keine Preisdaten in der DB.')
            return
        span_days = (date.today() - oldest.date).days + 30
        print(f'Ältester Preis: {oldest.date} → lade FX-Historie für {span_days} Tage')

        rates = fetch_exchange_rates()
        fx_history = fetch_fx_history(days=span_days)
        created = store_fx_history(fx_history)
        print(f'FX-Historie: {len(fx_history)} Währungen, {created} neue DB-Einträge')
        fx_lookup = FxLookup(fx_history, rates)

        stocks = Stock.query.order_by(Stock.symbol.asc())
        if args.symbols:
            stocks = stocks.filter(Stock.symbol.in_(args.symbols))

        total_changed = 0
        total_rows = 0
        for stock in stocks.all():
            currency = stock.currency or 'EUR'
            prices = Price.query.filter_by(stock_id=stock.id).all()
            if not prices:
                continue

            changed = 0
            max_rel_diff = 0.0
            for price in prices:
                new_eur = close_to_eur(stock.symbol, price.close, fx_lookup.rate(currency, price.date))
                old_eur = price.close_eur
                if old_eur and old_eur != 0:
                    rel_diff = abs(new_eur - old_eur) / abs(old_eur)
                else:
                    rel_diff = 1.0 if new_eur else 0.0
                if rel_diff > args.tolerance:
                    changed += 1
                    max_rel_diff = max(max_rel_diff, rel_diff)
                    if not args.dry_run:
                        price.close_eur = new_eur

            total_rows += len(prices)
            if changed:
                total_changed += changed
                print(f'- {stock.symbol} ({currency}): {changed}/{len(prices)} Zeilen, '
                      f'max. Abweichung {max_rel_diff * 100:.2f}%')
                if not args.dry_run:
                    db.session.commit()

        print('\n=== Ergebnis ===')
        mode = 'DRY-RUN, nichts geschrieben' if args.dry_run else 'geschrieben'
        print(f'Geänderte Zeilen: {total_changed} von {total_rows} ({mode})')


if __name__ == '__main__':
    main()
