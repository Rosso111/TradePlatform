"""
Data Fetcher Service
Lädt historische Kursdaten und Wechselkurse via yfinance.
"""
import logging
from datetime import date, datetime, timedelta, timezone

import yfinance as yf
import pandas as pd

log = logging.getLogger(__name__)


# ─── Wechselkurse ────────────────────────────────────────────────────────────

def fetch_exchange_rates() -> dict:
    """
    Liefert aktuelle Wechselkurse (Fremdwährung pro 1 EUR).
    z.B. {'USD': 1.08, 'GBP': 0.85, 'JPY': 163.5, ...}
    """
    pairs = {
        'USD': 'EURUSD=X',
        'GBP': 'EURGBP=X',
        'JPY': 'EURJPY=X',
        'CHF': 'EURCHF=X',
        'HKD': 'EURHKD=X',
        'KRW': 'EURKRW=X',
        'AUD': 'EURAUD=X',
    }
    rates = {'EUR': 1.0}
    for currency, pair in pairs.items():
        try:
            ticker = yf.Ticker(pair)
            hist = ticker.history(period='2d')
            if not hist.empty:
                rates[currency] = float(hist['Close'].iloc[-1])
        except Exception as e:
            log.warning(f"Wechselkurs {pair} nicht abrufbar: {e}")
            # Fallback-Kurse
            fallback = {'USD': 1.08, 'GBP': 0.85, 'JPY': 163.0,
                        'CHF': 0.96, 'HKD': 8.45, 'KRW': 1450.0, 'AUD': 1.65}
            rates[currency] = fallback.get(currency, 1.0)
    return rates


def to_eur(amount: float, currency: str, rates: dict) -> float:
    """Betrag in Fremdwährung → EUR"""
    if currency == 'EUR':
        return amount
    rate = rates.get(currency, 1.0)
    if rate == 0:
        return amount
    return amount / rate


# ─── Kursdaten ───────────────────────────────────────────────────────────────

def fetch_historical_prices(symbol: str, days: int = 400) -> pd.DataFrame:
    """
    Lädt historische OHLCV-Daten für ein Symbol.
    Gibt DataFrame mit Spalten [Open, High, Low, Close, Volume] zurück.
    Index = Date.
    """
    try:
        end = datetime.now()
        start = end - timedelta(days=days)
        ticker = yf.Ticker(symbol)
        df = ticker.history(start=start, end=end, auto_adjust=True)

        if df.empty:
            log.warning(f"Keine Kursdaten für {symbol}")
            return pd.DataFrame()

        df = df[['Open', 'High', 'Low', 'Close', 'Volume']].copy()
        df.index = pd.to_datetime(df.index).date
        df = df[~df.index.duplicated(keep='last')]
        df = df.sort_index()
        df.dropna(subset=['Close'], inplace=True)
        return df

    except Exception as e:
        log.error(f"Fehler beim Laden von {symbol}: {e}")
        return pd.DataFrame()


def fetch_current_price(symbol: str) -> float | None:
    """Aktueller Kurs eines Symbols"""
    try:
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period='1d')
        if not hist.empty:
            return float(hist['Close'].iloc[-1])
        info = ticker.info
        return info.get('currentPrice') or info.get('regularMarketPrice')
    except Exception as e:
        log.warning(f"Aktueller Kurs für {symbol} nicht abrufbar: {e}")
        return None


def fetch_analyst_recommendation(symbol: str) -> float:
    """
    Analyst-Empfehlung als Score 0-100.
    strongBuy=90, buy=75, hold=50, sell=25, strongSell=10
    """
    try:
        ticker = yf.Ticker(symbol)
        info = ticker.info
        rec = info.get('recommendationKey', 'hold').lower()
        mapping = {
            'strong_buy': 90, 'strongbuy': 90,
            'buy': 75,
            'hold': 50,
            'underperform': 35,
            'sell': 25,
            'strong_sell': 10, 'strongsell': 10,
        }
        return mapping.get(rec, 50.0)
    except Exception:
        return 50.0


# ─── Bulk-Operationen ────────────────────────────────────────────────────────

def fetch_multiple_prices(symbols: list[str], days: int = 400) -> dict[str, pd.DataFrame]:
    """
    Lädt Kursdaten für mehrere Symbole auf einmal (effizienter als Einzelabrufe).
    """
    result = {}
    # yfinance download für Batch-Abruf
    try:
        end = datetime.now()
        start = end - timedelta(days=days)
        raw = yf.download(
            symbols, start=start, end=end,
            auto_adjust=True, group_by='ticker',
            progress=False, threads=True
        )
        if raw.empty:
            raise ValueError("Leerer Datensatz")

        for symbol in symbols:
            try:
                if len(symbols) == 1:
                    df = raw[['Open', 'High', 'Low', 'Close', 'Volume']].copy()
                else:
                    df = raw[symbol][['Open', 'High', 'Low', 'Close', 'Volume']].copy()
                df.index = pd.to_datetime(df.index).date
                df = df[~df.index.duplicated(keep='last')].sort_index()
                df.dropna(subset=['Close'], inplace=True)
                if not df.empty:
                    result[symbol] = df
            except Exception as e:
                log.warning(f"Batch-Parse für {symbol} fehlgeschlagen: {e}")

    except Exception as e:
        log.warning(f"Batch-Download fehlgeschlagen ({e}), versuche Einzelabruf")
        for symbol in symbols:
            df = fetch_historical_prices(symbol, days)
            if not df.empty:
                result[symbol] = df

    return result


def store_prices_to_db(app, stock_universe: list[dict], days: int = 400):
    """
    Lädt alle Kursdaten und speichert sie in der Datenbank.
    Wird beim Start einmalig ausgeführt und dann inkrementell aktualisiert.
    """
    from models import db, Stock, Price, ExchangeRate

    with app.app_context():
        # 1. Wechselkurse laden
        rates = fetch_exchange_rates()
        today = date.today()
        for currency, rate in rates.items():
            if currency == 'EUR':
                continue
            pair = f'EUR{currency}'
            existing = ExchangeRate.query.filter_by(pair=pair, date=today).first()
            if not existing:
                db.session.add(ExchangeRate(pair=pair, date=today, rate=rate))
        db.session.commit()
        log.info(f"Wechselkurse gespeichert: {rates}")

        # 2. Aktien im Universum sicherstellen
        for stock_info in stock_universe:
            existing = Stock.query.filter_by(symbol=stock_info['symbol']).first()
            if not existing:
                db.session.add(Stock(
                    symbol=stock_info['symbol'],
                    name=stock_info['name'],
                    sector=stock_info['sector'],
                    region=stock_info['region'],
                    currency=stock_info['currency'],
                ))
        db.session.commit()

        # 3. Kursdaten laden und speichern
        symbols = [s['symbol'] for s in stock_universe]
        log.info(f"Lade Kursdaten für {len(symbols)} Symbole...")

        # Batch-Gruppen von 20 (API-Limits)
        for i in range(0, len(symbols), 20):
            batch = symbols[i:i+20]
            price_data = fetch_multiple_prices(batch, days)

            for symbol, df in price_data.items():
                stock = Stock.query.filter_by(symbol=symbol).first()
                if not stock:
                    continue

                currency = next(
                    (s['currency'] for s in stock_universe if s['symbol'] == symbol), 'EUR'
                )
                fx_rate = rates.get(currency, 1.0)

                # Nur neue Tage einfügen
                existing_dates = {
                    p.date for p in Price.query.filter_by(stock_id=stock.id).all()
                }

                new_prices = []
                for idx_date, row in df.iterrows():
                    if idx_date not in existing_dates:
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
                    log.info(f"{symbol}: {len(new_prices)} neue Kursdatensätze gespeichert")

            db.session.commit()

        log.info("Kursdaten erfolgreich geladen und gespeichert.")


def update_prices_incremental(app, stock_universe: list[dict]):
    """
    Inkrementelle Aktualisierung: nur die letzten 5 Tage nachladen.
    """
    store_prices_to_db(app, stock_universe, days=5)
