"""
Data Fetcher Service
Lädt historische Kursdaten und Wechselkurse via yfinance.
"""
import bisect
import logging
import threading
from datetime import date, datetime, timedelta, timezone

import yfinance as yf
import pandas as pd

log = logging.getLogger(__name__)

# Drossel für Preis-Updates: Paper- und IBKR-Zyklus rufen beide alle
# TRADING_INTERVAL_MINUTES update_prices_incremental() auf, die Tages-
# Kursdaten ändern sich aber höchstens einmal pro Handelstag.
_price_update_lock = threading.Lock()
_last_price_update: datetime | None = None

# Kurzzeit-Cache für Spot-Wechselkurse (verhindert doppelte Abrufe
# innerhalb desselben Handelszyklus)
_fx_cache_lock = threading.Lock()
_fx_cache: tuple[datetime, dict] | None = None
_FX_CACHE_TTL = timedelta(minutes=5)


# ─── Wechselkurse ────────────────────────────────────────────────────────────

_HARDCODED_FALLBACK = {
    'USD': 1.08, 'GBP': 0.85, 'JPY': 163.0, 'CHF': 0.96,
    'HKD': 8.45, 'KRW': 1450.0, 'AUD': 1.65, 'SEK': 11.5,
    'NOK': 11.8, 'DKK': 7.46, 'CAD': 1.55, 'CNY': 7.90,
}

_PAIRS = {
    'USD': 'EURUSD=X', 'GBP': 'EURGBP=X', 'JPY': 'EURJPY=X',
    'CHF': 'EURCHF=X', 'HKD': 'EURHKD=X', 'KRW': 'EURKRW=X',
    'AUD': 'EURAUD=X', 'SEK': 'EURSEK=X', 'NOK': 'EURNOK=X',
    'DKK': 'EURDKK=X', 'CAD': 'EURCAD=X', 'CNY': 'EURCNY=X',
}


def _db_fallback_rate(currency: str) -> float | None:
    """Letzten bekannten Kurs aus der DB lesen. Gibt None zurück wenn nicht verfügbar."""
    try:
        from models import ExchangeRate
        row = (ExchangeRate.query
               .filter_by(pair=f'EUR{currency}')
               .order_by(ExchangeRate.date.desc())
               .first())
        if row and row.rate and row.rate > 0:
            return row.rate
    except Exception:
        pass
    return None


def fetch_exchange_rates() -> dict:
    """
    Liefert aktuelle Wechselkurse (Fremdwährung pro 1 EUR).
    z.B. {'USD': 1.08, 'GBP': 0.85, 'JPY': 163.5, ...}
    Fallback-Reihenfolge bei yfinance-Ausfall: DB → hardcodierter Näherungswert.
    Ergebnisse werden kurz gecacht (_FX_CACHE_TTL), damit Paper- und
    IBKR-Zyklus im selben Takt nicht doppelt alle Paare abrufen.
    """
    global _fx_cache
    with _fx_cache_lock:
        if _fx_cache and datetime.now() - _fx_cache[0] < _FX_CACHE_TTL:
            return dict(_fx_cache[1])

    rates = {'EUR': 1.0}
    for currency, pair in _PAIRS.items():
        try:
            ticker = yf.Ticker(pair)
            hist = ticker.history(period='2d')
            if not hist.empty:
                rates[currency] = float(hist['Close'].iloc[-1])
            else:
                raise ValueError(f"Leere Historie für {pair}")
        except Exception as e:
            db_rate = _db_fallback_rate(currency)
            if db_rate is not None:
                rates[currency] = db_rate
                log.warning("Wechselkurs %s nicht abrufbar (%s) — DB-Fallback: %.4f", pair, e, db_rate)
            else:
                fallback = _HARDCODED_FALLBACK.get(currency, 1.0)
                rates[currency] = fallback
                log.error("Wechselkurs %s nicht abrufbar und kein DB-Eintrag — Näherungswert %.4f", pair, fallback)

    with _fx_cache_lock:
        _fx_cache = (datetime.now(), dict(rates))
    return rates


def fetch_fx_history(days: int = 400) -> dict[str, pd.Series]:
    """
    Historische EUR-Wechselkurse (Fremdwährung pro 1 EUR) je Währung.
    Gibt {currency: Series(date -> rate)} zurück; nicht abrufbare Paare fehlen im Dict.
    """
    history: dict[str, pd.Series] = {}
    end = datetime.now()
    # Puffer, damit auch für den ersten Handelstag ein Kurs davor existiert
    start = end - timedelta(days=days + 14)

    raw = pd.DataFrame()
    try:
        raw = yf.download(
            list(_PAIRS.values()), start=start, end=end,
            auto_adjust=True, group_by='ticker',
            progress=False, threads=True
        )
    except Exception as e:
        log.warning(f"FX-Historie Batch-Download fehlgeschlagen ({e}), versuche Einzelabrufe")

    for currency, pair in _PAIRS.items():
        series = None
        try:
            if not raw.empty:
                if isinstance(raw.columns, pd.MultiIndex):
                    if pair in raw.columns.get_level_values(0):
                        series = raw[pair]['Close'].dropna()
                else:
                    series = raw['Close'].dropna()
            if series is None or series.empty:
                series = yf.Ticker(pair).history(start=start, end=end)['Close'].dropna()
        except Exception as e:
            log.warning(f"FX-Historie für {pair} nicht abrufbar: {e}")
            continue
        if series.empty:
            log.warning(f"FX-Historie für {pair} leer")
            continue
        series = series.copy()
        series.index = pd.to_datetime(series.index).date
        series = series[~series.index.duplicated(keep='last')].sort_index()
        history[currency] = series
    return history


def store_fx_history(fx_history: dict[str, pd.Series]) -> int:
    """Speichert historische Wechselkurse in die exchange_rates-Tabelle (nur fehlende Tage)."""
    from models import db, ExchangeRate
    created = 0
    for currency, series in fx_history.items():
        pair = f'EUR{currency}'
        existing = {
            row.date for row in
            ExchangeRate.query.with_entities(ExchangeRate.date).filter_by(pair=pair).all()
        }
        for d, rate in series.items():
            if d not in existing and rate and rate > 0:
                db.session.add(ExchangeRate(pair=pair, date=d, rate=float(rate)))
                created += 1
    if created:
        db.session.commit()
        log.info(f"FX-Historie: {created} neue Kurs-Einträge gespeichert")
    return created


class FxLookup:
    """
    Datums-Lookup für historische Wechselkurse: liefert den letzten
    verfügbaren Kurs <= Stichtag (Wochenenden/Feiertage), sonst den Fallback
    aus den aktuellen Kursen.
    """

    def __init__(self, fx_history: dict[str, pd.Series], fallback_rates: dict):
        self._data = {
            currency: (list(series.index), list(series.values))
            for currency, series in fx_history.items()
        }
        self._fallback = fallback_rates

    def rate(self, currency: str, d: date) -> float:
        if currency == 'EUR':
            return 1.0
        entry = self._data.get(currency)
        if entry:
            dates, values = entry
            i = bisect.bisect_right(dates, d) - 1
            if i >= 0:
                return float(values[i])
        return float(self._fallback.get(currency, 1.0))


def close_to_eur(symbol: str, close_val: float, fx_rate: float) -> float:
    """
    Schlusskurs → EUR. LSE-Symbole (.L) liefert yfinance in GBX (Pence),
    daher zusätzlich Division durch 100.
    """
    gbx_divisor = 100.0 if symbol.endswith('.L') else 1.0
    value = close_val / gbx_divisor
    return value / fx_rate if fx_rate > 0 else value


def to_eur(amount: float, currency: str, rates: dict) -> float:
    """Betrag in Fremdwährung → EUR"""
    if currency == 'EUR':
        return amount
    rate = rates.get(currency)
    if not rate:
        log.warning("Kein Wechselkurs für %s — EUR-Umrechnung nicht möglich, Betrag unverändert", currency)
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

def _normalize_price_df(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    df = df[['Open', 'High', 'Low', 'Close', 'Volume']].copy()
    df.index = pd.to_datetime(df.index).date
    df = df[~df.index.duplicated(keep='last')].sort_index()
    df.dropna(subset=['Close'], inplace=True)
    return df


def fetch_multiple_prices(symbols: list[str], days: int = 400) -> dict[str, pd.DataFrame]:
    """
    Lädt Kursdaten für mehrere Symbole auf einmal (effizienter als Einzelabrufe).
    Fällt pro Symbol auf Einzelabruf zurück, wenn Batch-Daten fehlen oder nicht parsebar sind.
    """
    result = {}
    missing_symbols = []
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

        single_symbol_mode = len(symbols) == 1 or not isinstance(raw.columns, pd.MultiIndex)

        for symbol in symbols:
            try:
                if single_symbol_mode:
                    df = _normalize_price_df(raw)
                else:
                    if symbol not in raw.columns.get_level_values(0):
                        missing_symbols.append(symbol)
                        log.warning(f"Batch-Daten für {symbol} fehlen; versuche Einzelabruf")
                        continue
                    df = _normalize_price_df(raw[symbol])
                if not df.empty:
                    result[symbol] = df
                else:
                    missing_symbols.append(symbol)
                    log.warning(f"Batch-Daten für {symbol} leer; versuche Einzelabruf")
            except Exception as e:
                missing_symbols.append(symbol)
                log.warning(f"Batch-Parse für {symbol} fehlgeschlagen: {e}; versuche Einzelabruf")

    except Exception as e:
        log.warning(f"Batch-Download fehlgeschlagen ({e}), versuche Einzelabruf für alle Symbole")
        missing_symbols = list(symbols)

    for symbol in missing_symbols:
        if symbol in result:
            continue
        df = fetch_historical_prices(symbol, days)
        if not df.empty:
            result[symbol] = df
        else:
            log.warning(f"Einzelabruf für {symbol} lieferte ebenfalls keine Daten")

    return result


def store_prices_to_db(app, stock_universe: list[dict], days: int = 400):
    """
    Lädt alle Kursdaten und speichert sie in der Datenbank.
    Wird beim Start einmalig ausgeführt und dann inkrementell aktualisiert.
    """
    from models import db, Stock, Price, ExchangeRate

    with app.app_context():
        # 1. Wechselkurse laden (aktuell + Historie für datumsgenaue EUR-Umrechnung)
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

        fx_history = fetch_fx_history(days)
        store_fx_history(fx_history)
        fx_lookup = FxLookup(fx_history, rates)

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

                currency = stock.currency or next(
                    (s['currency'] for s in stock_universe if s['symbol'] == symbol), 'EUR'
                )

                # Nur neue Tage einfügen — Abgleich nur im geladenen Zeitfenster,
                # nicht über die gesamte Historie (bis zu ~7000 Zeilen pro Aktie)
                min_fetched = min(df.index)
                existing_dates = {
                    row.date for row in
                    Price.query.with_entities(Price.date)
                    .filter(Price.stock_id == stock.id, Price.date >= min_fetched)
                    .all()
                }

                new_prices = []
                for idx_date, row in df.iterrows():
                    if idx_date not in existing_dates:
                        close_val = float(row['Close'])
                        close_eur = close_to_eur(symbol, close_val, fx_lookup.rate(currency, idx_date))
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


def _with_position_stocks(app, stock_universe: list[dict]) -> list[dict]:
    """Ergänzt Aktien mit offener Position, die nicht (mehr) im Universum stehen.

    Positionen aus IBKR-Importen können außerhalb von STOCK_UNIVERSE liegen —
    ohne Kurs-Updates liefe ihre SL/TP-Überwachung auf eingefrorenen Daten.
    """
    from models import Position, Stock

    known = {s['symbol'] for s in stock_universe}
    with app.app_context():
        rows = (Stock.query
                .join(Position, Position.stock_id == Stock.id)
                .filter(Stock.symbol.notin_(known))
                .distinct()
                .all())
        extra = [{'symbol': s.symbol, 'name': s.name or s.symbol,
                  'sector': s.sector or 'Unbekannt', 'region': s.region or 'US',
                  'currency': s.currency or 'USD'} for s in rows]
    if extra:
        log.info("Preis-Update: %d Positions-Aktien außerhalb des Universums ergänzt: %s",
                 len(extra), ', '.join(sorted(e['symbol'] for e in extra)))
    return stock_universe + extra


def update_prices_incremental(app, stock_universe: list[dict], force: bool = False) -> bool:
    """
    Inkrementelle Aktualisierung: nur die letzten 5 Tage nachladen.

    Läuft höchstens alle DATA_UPDATE_INTERVAL_HOURS (config) — der
    Handelszyklus ruft alle TRADING_INTERVAL_MINUTES hier auf (Paper- und
    IBKR-Pfad je einmal), soll aber nicht jedes Mal das komplette Universum
    von yfinance laden. Gibt True zurück, wenn tatsächlich geladen wurde.
    """
    global _last_price_update
    import config

    if not _price_update_lock.acquire(blocking=False):
        log.info("Preis-Update läuft bereits in anderem Thread — übersprungen")
        return False
    try:
        min_interval = timedelta(hours=config.DATA_UPDATE_INTERVAL_HOURS)
        now = datetime.now()
        if not force and _last_price_update and now - _last_price_update < min_interval:
            log.debug("Preis-Update übersprungen — letztes Update %s", _last_price_update)
            return False
        store_prices_to_db(app, _with_position_stocks(app, stock_universe), days=5)
        _last_price_update = datetime.now()
        return True
    finally:
        _price_update_lock.release()
