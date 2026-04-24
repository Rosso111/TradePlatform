#!/usr/bin/env python3
"""Lädt historische Kursdaten gezielt nach und speichert nur fehlende Preiszeilen.

Beispiele:
  python scripts/backfill_price_history.py --years 10 --universe global_core
  python scripts/backfill_price_history.py --years 5 --symbols AAPL MSFT NVDA
  python scripts/backfill_price_history.py --years 10 --all-active
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
from models import db, Stock, Price, ExchangeRate
from services.data_fetcher import fetch_exchange_rates, fetch_multiple_prices, fetch_historical_prices
from services.universe_store import get_universe


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Historische Kursdaten per yfinance nachladen')
    parser.add_argument('--years', type=int, default=10, help='Wie viele Jahre rückwirkend laden (Standard: 10)')
    parser.add_argument('--universe', help='Universe-ID aus data/universes.json')
    parser.add_argument('--symbols', nargs='*', default=[], help='Explizite Symbole')
    parser.add_argument('--all-active', action='store_true', help='Alle aktiven Aktien aus der DB laden')
    parser.add_argument('--batch-size', type=int, default=10, help='Batch-Größe für yfinance-Downloads')
    return parser.parse_args()


def build_stock_lookup():
    return {item['symbol']: item for item in config.STOCK_UNIVERSE}


def resolve_symbols(args: argparse.Namespace):
    if args.symbols:
        return args.symbols
    if args.universe:
        universe = get_universe(args.universe)
        if not universe:
            raise SystemExit(f'Universe {args.universe!r} nicht gefunden')
        return universe.get('symbols') or []
    if args.all_active:
        return [row.symbol for row in Stock.query.filter_by(active=True).order_by(Stock.symbol.asc()).all()]
    raise SystemExit('Bitte --universe, --symbols oder --all-active angeben')


def ensure_exchange_rates(rates: dict):
    today = date.today()
    created = 0
    for currency, rate in rates.items():
        if currency == 'EUR':
            continue
        pair = f'EUR{currency}'
        existing = ExchangeRate.query.filter_by(pair=pair, date=today).first()
        if not existing:
            db.session.add(ExchangeRate(pair=pair, date=today, rate=rate))
            created += 1
    if created:
        db.session.commit()
    return created


def ensure_stock(symbol: str, stock_lookup: dict):
    stock = Stock.query.filter_by(symbol=symbol).first()
    if stock:
        return stock

    info = stock_lookup.get(symbol)
    if not info:
        raise ValueError(f'Symbol {symbol} ist weder in der DB noch in config.STOCK_UNIVERSE bekannt')

    stock = Stock(
        symbol=info['symbol'],
        name=info['name'],
        sector=info['sector'],
        region=info['region'],
        currency=info['currency'],
        active=True,
    )
    db.session.add(stock)
    db.session.commit()
    return stock


def main():
    args = parse_args()
    days = max(args.years * 365 + 30, 30)
    app = create_app()

    with app.app_context():
        stock_lookup = build_stock_lookup()
        symbols = resolve_symbols(args)
        if not symbols:
            print('Keine Symbole ausgewählt.')
            return

        rates = fetch_exchange_rates()
        fx_created = ensure_exchange_rates(rates)
        print(f'Wechselkurse geprüft/angelegt: {fx_created}')
        print(f'Symbole: {len(symbols)} | Jahre: {args.years} | Tage-Fenster: {days}')

        total_new_rows = 0
        success_symbols = 0
        failed_symbols = []

        for start in range(0, len(symbols), args.batch_size):
            batch = symbols[start:start + args.batch_size]
            print(f'\nBatch {start + 1}-{start + len(batch)} / {len(symbols)}: {", ".join(batch)}')
            price_data = fetch_multiple_prices(batch, days=days)

            for symbol in batch:
                try:
                    stock = ensure_stock(symbol, stock_lookup)
                    currency = stock.currency or stock_lookup.get(symbol, {}).get('currency', 'EUR')
                    fx_rate = rates.get(currency, 1.0)
                    df = price_data.get(symbol)
                    if df is None or df.empty:
                        df = fetch_historical_prices(symbol, days=days)
                    if df is None or df.empty:
                        failed_symbols.append(symbol)
                        print(f'- {symbol}: keine Daten erhalten (Batch + Einzelabruf)')
                        continue

                    existing_dates = {
                        row.date for row in Price.query.with_entities(Price.date).filter_by(stock_id=stock.id).all()
                    }
                    new_prices = []
                    for idx_date, row in df.iterrows():
                        if idx_date in existing_dates:
                            continue
                        close_val = float(row['Close'])
                        close_eur = close_val / fx_rate if fx_rate > 0 else close_val
                        new_prices.append(Price(
                            stock_id=stock.id,
                            date=idx_date,
                            open=float(row['Open']),
                            high=float(row['High']),
                            low=float(row['Low']),
                            close=close_val,
                            volume=int(row.get('Volume', 0) or 0),
                            close_eur=close_eur,
                        ))

                    if new_prices:
                        db.session.bulk_save_objects(new_prices)
                        db.session.commit()
                        total_new_rows += len(new_prices)
                        print(f'- {symbol}: {len(new_prices)} neue Zeilen gespeichert')
                    else:
                        print(f'- {symbol}: keine neuen Zeilen')
                    success_symbols += 1
                except Exception as e:
                    db.session.rollback()
                    failed_symbols.append(symbol)
                    print(f'- {symbol}: Fehler: {e}')

        print('\n=== Ergebnis ===')
        print(f'Erfolgreiche Symbole: {success_symbols}')
        print(f'Fehlgeschlagene Symbole: {len(failed_symbols)}')
        if failed_symbols:
            print('Fehlerhafte Symbole:', ', '.join(sorted(set(failed_symbols))))
        print(f'Neue Preiszeilen gesamt: {total_new_rows}')


if __name__ == '__main__':
    main()
