"""
Algorithmus-Engine
Berechnet technische Indikatoren, führt Backtesting durch und
generiert Kaufs-/Verkaufssignale mit einem Score 0-100.
"""
import logging
from datetime import date, timedelta
from itertools import product

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)


# ─── Technische Indikatoren ──────────────────────────────────────────────────

def calc_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def calc_macd(close: pd.Series, fast=12, slow=26, signal=9):
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def calc_bollinger(close: pd.Series, period=20, std_dev=2.0):
    sma = close.rolling(period).mean()
    std = close.rolling(period).std()
    upper = sma + std_dev * std
    lower = sma - std_dev * std
    return upper, sma, lower


def calc_atr(high: pd.Series, low: pd.Series, close: pd.Series, period=14) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs()
    ], axis=1).max(axis=1)
    return tr.ewm(com=period - 1, min_periods=period).mean()


def calc_volume_score(volume: pd.Series, period=20) -> pd.Series:
    """Volumen-Score: aktuelles Volumen vs. Durchschnitt (0-100)"""
    avg_vol = volume.rolling(period).mean()
    ratio = volume / avg_vol.replace(0, np.nan)
    return ratio.clip(0, 3) / 3 * 100


def add_indicators(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    """Alle Indikatoren zu einem OHLCV-DataFrame hinzufügen"""
    df = df.copy()
    close = df['Close']
    high = df['High']
    low = df['Low']
    volume = df.get('Volume', pd.Series(dtype=float))

    rsi_p = params.get('rsi_period', 14)
    ema_f = params.get('ema_fast', 20)
    ema_s = params.get('ema_slow', 50)
    macd_f = params.get('macd_fast', 12)
    macd_sl = params.get('macd_slow', 26)
    macd_sig = params.get('macd_signal', 9)
    bb_p = params.get('bb_period', 20)
    bb_std = params.get('bb_std', 2.0)

    df['rsi'] = calc_rsi(close, rsi_p)
    df['ema_fast'] = close.ewm(span=ema_f, adjust=False).mean()
    df['ema_slow'] = close.ewm(span=ema_s, adjust=False).mean()
    df['macd'], df['macd_signal'], df['macd_hist'] = calc_macd(close, macd_f, macd_sl, macd_sig)
    df['bb_upper'], df['bb_mid'], df['bb_lower'] = calc_bollinger(close, bb_p, bb_std)
    df['atr'] = calc_atr(high, low, close)
    df['atr_pct'] = df['atr'] / close * 100
    if not volume.empty and volume.sum() > 0:
        df['vol_score'] = calc_volume_score(volume)
    else:
        df['vol_score'] = 50.0

    return df


# ─── Signal-Scoring ──────────────────────────────────────────────────────────

def compute_score(row: pd.Series, params: dict,
                  analyst_score: float = 50.0,
                  sector_score: float = 50.0) -> float:
    """
    Kombinierter Signal-Score 0-100.
    > 65 = Kaufsignal, < 35 = Verkaufssignal, 35-65 = Halten

    Gewichtung:
      - RSI-Score          25%
      - MACD-Score         20%
      - EMA-Crossover      20%
      - Bollinger-Score    15%
      - Analyst-Score      10%
      - Sektor-Score       10%
    """
    score = 0.0

    # 1. RSI (25 Punkte)
    rsi = row.get('rsi')
    if rsi is not None and not np.isnan(rsi):
        oversold = params.get('rsi_oversold', 35)
        overbought = params.get('rsi_overbought', 65)
        if rsi < oversold:
            # Stark überverkauft → bullisch
            rsi_score = 80 + (oversold - rsi) / oversold * 20
        elif rsi > overbought:
            # Stark überkauft → bearisch
            rsi_score = 20 - (rsi - overbought) / (100 - overbought) * 20
        else:
            # Neutral: 50er-Bereich = 50, Mitte neutral
            rsi_score = 50 + (50 - rsi) * 0.5
        score += min(max(rsi_score, 0), 100) * 0.25

    # 2. MACD (20 Punkte)
    macd = row.get('macd')
    macd_sig = row.get('macd_signal')
    macd_hist = row.get('macd_hist')
    if all(v is not None and not np.isnan(v) for v in [macd, macd_sig, macd_hist]):
        if macd > macd_sig:
            # MACD über Signal = bullisch
            macd_score = 65 + min(abs(macd_hist) / max(abs(macd), 0.001) * 35, 35)
        else:
            macd_score = 35 - min(abs(macd_hist) / max(abs(macd), 0.001) * 35, 35)
        score += min(max(macd_score, 0), 100) * 0.20

    # 3. EMA-Crossover (20 Punkte)
    ema_f = row.get('ema_fast')
    ema_s = row.get('ema_slow')
    close = row.get('Close')
    if all(v is not None and not np.isnan(v) for v in [ema_f, ema_s, close]):
        if ema_f > ema_s:
            gap_pct = (ema_f - ema_s) / ema_s * 100
            ema_score = 60 + min(gap_pct * 10, 40)
        else:
            gap_pct = (ema_s - ema_f) / ema_s * 100
            ema_score = 40 - min(gap_pct * 10, 40)
        # Kurs über/unter EMA-Fast
        if close > ema_f:
            ema_score = min(ema_score + 5, 100)
        else:
            ema_score = max(ema_score - 5, 0)
        score += min(max(ema_score, 0), 100) * 0.20

    # 4. Bollinger Bands (15 Punkte)
    bb_up = row.get('bb_upper')
    bb_lo = row.get('bb_lower')
    bb_mid = row.get('bb_mid')
    if all(v is not None and not np.isnan(v) for v in [bb_up, bb_lo, bb_mid, close]):
        band_width = bb_up - bb_lo
        if band_width > 0:
            position = (close - bb_lo) / band_width  # 0=unteres Band, 1=oberes Band
            if position < 0.2:
                bb_score = 80 + (0.2 - position) / 0.2 * 20
            elif position > 0.8:
                bb_score = 20 - (position - 0.8) / 0.2 * 20
            else:
                bb_score = 50
        else:
            bb_score = 50
        score += min(max(bb_score, 0), 100) * 0.15

    # 5. Analyst-Empfehlung (10 Punkte)
    score += analyst_score * 0.10

    # 6. Sektor-Momentum (10 Punkte)
    score += sector_score * 0.10

    return min(max(score, 0), 100)


# ─── Sektor-Momentum ─────────────────────────────────────────────────────────

def compute_sector_scores(app) -> dict[str, float]:
    """
    Berechnet Sektor-Momentum-Score für jeden Sektor.
    Basis: Durchschnittliche Kursveränderung der letzten 20 Tage.
    """
    from models import db, Stock, Price

    with app.app_context():
        sectors = {}
        stocks = Stock.query.filter_by(active=True).all()

        for stock in stocks:
            prices = (Price.query
                      .filter_by(stock_id=stock.id)
                      .order_by(Price.date.desc())
                      .limit(25).all())
            if len(prices) < 2:
                continue
            prices = sorted(prices, key=lambda p: p.date)
            oldest = prices[0].close_eur or prices[0].close
            newest = prices[-1].close_eur or prices[-1].close
            if oldest and oldest > 0:
                change_pct = (newest - oldest) / oldest * 100
                if stock.sector not in sectors:
                    sectors[stock.sector] = []
                sectors[stock.sector].append(change_pct)

        result = {}
        for sector, changes in sectors.items():
            avg_change = sum(changes) / len(changes)
            # Normalisieren: -10% → 20, 0% → 50, +10% → 80
            score = 50 + avg_change * 3
            result[sector] = min(max(score, 10), 90)

        return result


# ─── Backtesting & Parameter-Optimierung ─────────────────────────────────────

def backtest_strategy(df: pd.DataFrame, params: dict,
                      commission_rate: float = 0.001) -> dict:
    """
    Simpler Backtest einer Strategie auf historischen Daten.
    Gibt Sharpe-Ratio, Gesamtrendite und Win-Rate zurück.
    """
    if len(df) < 60:
        return {'sharpe': 0.0, 'total_return': 0.0, 'win_rate': 0.0, 'trades': 0}

    df = add_indicators(df, params)
    df = df.dropna()

    capital = 1000.0
    position = 0.0
    entry_price = 0.0
    returns = []
    trades = []

    for i in range(1, len(df)):
        row = df.iloc[i]
        prev = df.iloc[i - 1]
        score = compute_score(row, params)

        if position == 0 and score >= 65:
            # Kaufen
            cost = capital * 0.95
            commission = cost * commission_rate
            shares = (cost - commission) / row['Close']
            position = shares
            entry_price = row['Close']
            capital -= cost

        elif position > 0 and (score <= 35 or
                                row['Close'] < entry_price * 0.95):  # 5% Stop-Loss
            # Verkaufen
            revenue = position * row['Close']
            commission = revenue * commission_rate
            pnl = revenue - commission - (position * entry_price)
            trades.append(pnl > 0)
            daily_ret = pnl / (position * entry_price)
            returns.append(daily_ret)
            capital += revenue - commission
            position = 0
            entry_price = 0

    # Offene Position schließen
    if position > 0:
        last_close = df.iloc[-1]['Close']
        revenue = position * last_close
        commission = revenue * commission_rate
        pnl = revenue - commission - (position * entry_price)
        trades.append(pnl > 0)
        returns.append(pnl / (position * entry_price) if entry_price > 0 else 0)
        capital += revenue - commission

    total_return = (capital - 1000.0) / 1000.0 * 100
    win_rate = (sum(trades) / len(trades) * 100) if trades else 0

    if len(returns) > 1:
        ret_arr = np.array(returns)
        sharpe = (np.mean(ret_arr) / np.std(ret_arr) * np.sqrt(252)
                  if np.std(ret_arr) > 0 else 0.0)
    else:
        sharpe = 0.0

    return {
        'sharpe': round(sharpe, 3),
        'total_return': round(total_return, 2),
        'win_rate': round(win_rate, 1),
        'trades': len(trades)
    }


def optimize_parameters(df: pd.DataFrame, commission_rate: float = 0.001) -> dict:
    """
    Grid-Search über Parameter-Kombinationen → beste Sharpe-Ratio.
    Reduziertes Grid für Performance.
    """
    if len(df) < 100:
        return _default_params()

    param_grid = {
        'rsi_period':    [10, 14],
        'rsi_oversold':  [30, 35],
        'rsi_overbought':[65, 70],
        'ema_fast':      [15, 20],
        'ema_slow':      [40, 50],
        'macd_fast':     [12],
        'macd_slow':     [26],
        'macd_signal':   [9],
        'bb_period':     [20],
        'bb_std':        [2.0],
    }

    best_sharpe = -999
    best_params = _default_params()

    for (rsi_p, rsi_os, rsi_ob, ema_f, ema_s) in product(
        param_grid['rsi_period'], param_grid['rsi_oversold'],
        param_grid['rsi_overbought'], param_grid['ema_fast'],
        param_grid['ema_slow']
    ):
        if rsi_os >= rsi_ob:
            continue
        if ema_f >= ema_s:
            continue
        params = {
            'rsi_period': rsi_p, 'rsi_oversold': rsi_os,
            'rsi_overbought': rsi_ob, 'ema_fast': ema_f,
            'ema_slow': ema_s, 'macd_fast': 12,
            'macd_slow': 26, 'macd_signal': 9,
            'bb_period': 20, 'bb_std': 2.0,
        }
        result = backtest_strategy(df, params, commission_rate)
        if result['sharpe'] > best_sharpe and result['trades'] >= 3:
            best_sharpe = result['sharpe']
            best_params = params.copy()
            best_params['_backtest'] = result

    log.info(f"Optimale Parameter: Sharpe={best_sharpe:.2f}, "
             f"Return={best_params.get('_backtest', {}).get('total_return', 0):.1f}%")
    return best_params


def _default_params() -> dict:
    return {
        'rsi_period': 14, 'rsi_oversold': 35, 'rsi_overbought': 65,
        'ema_fast': 20, 'ema_slow': 50, 'macd_fast': 12,
        'macd_slow': 26, 'macd_signal': 9, 'bb_period': 20, 'bb_std': 2.0,
    }


# ─── Signal-Generierung für alle Aktien ──────────────────────────────────────

def generate_signals_for_date(app, as_of_date) -> list[dict]:
    """
    Berechnet Signale fuer alle aktiven Aktien mit Daten bis einschliesslich as_of_date.
    Speichert nur dann in die Signal-Tabelle, wenn as_of_date = heute ist.
    """
    from models import db, Stock, Price, Signal, AlgoParams

    sector_scores = compute_sector_scores(app)
    signals = []

    with app.app_context():
        stocks = Stock.query.filter_by(active=True).all()

        for stock in stocks:
            try:
                prices = (Price.query
                          .filter(Price.stock_id == stock.id, Price.date <= as_of_date)
                          .order_by(Price.date.asc())
                          .all())

                if len(prices) < 60:
                    continue

                df = pd.DataFrame([{
                    'Open': p.open, 'High': p.high, 'Low': p.low,
                    'Close': p.close, 'Volume': p.volume
                } for p in prices], index=[p.date for p in prices])

                algo = AlgoParams.query.filter_by(stock_id=stock.id).first()
                params = (
                    {
                        'rsi_period': algo.rsi_period, 'rsi_oversold': algo.rsi_oversold,
                        'rsi_overbought': algo.rsi_overbought, 'ema_fast': algo.ema_fast,
                        'ema_slow': algo.ema_slow, 'macd_fast': algo.macd_fast,
                        'macd_slow': algo.macd_slow, 'macd_signal': algo.macd_signal,
                        'bb_period': algo.bb_period, 'bb_std': algo.bb_std,
                    }
                    if algo else _default_params()
                )

                df = add_indicators(df, params)
                df = df.dropna()
                if df.empty:
                    continue

                last_row = df.iloc[-1]

                from services.data_fetcher import fetch_analyst_recommendation
                analyst_score = fetch_analyst_recommendation(stock.symbol)
                sector_score = sector_scores.get(stock.sector, 50.0)
                news_score = 50.0
                technical_score = compute_score(last_row, params, 50.0, sector_score)
                score = compute_score(last_row, params, analyst_score, sector_score)

                if score >= 65:
                    action = 'BUY'
                elif score <= 35:
                    action = 'SELL'
                else:
                    action = 'HOLD'

                if as_of_date == date.today():
                    existing = Signal.query.filter_by(
                        stock_id=stock.id, date=as_of_date
                    ).first()
                    sig_obj = existing or Signal(stock_id=stock.id, date=as_of_date)
                    sig_obj.score = score
                    sig_obj.action = action
                    sig_obj.rsi = float(last_row['rsi']) if not np.isnan(last_row['rsi']) else None
                    sig_obj.macd = float(last_row['macd'])
                    sig_obj.macd_signal = float(last_row['macd_signal'])
                    sig_obj.ema20 = float(last_row['ema_fast'])
                    sig_obj.ema50 = float(last_row['ema_slow'])
                    sig_obj.bb_upper = float(last_row['bb_upper'])
                    sig_obj.bb_lower = float(last_row['bb_lower'])
                    sig_obj.atr = float(last_row['atr']) if not np.isnan(last_row['atr']) else None
                    sig_obj.analyst_score = analyst_score
                    sig_obj.sector_score = sector_score
                    if not existing:
                        db.session.add(sig_obj)

                signals.append({
                    'stock_id': stock.id,
                    'symbol': stock.symbol,
                    'name': stock.name,
                    'sector': stock.sector,
                    'currency': stock.currency,
                    'score': score,
                    'action': action,
                    'current_price': float(prices[-1].close),
                    'current_price_eur': float(prices[-1].close_eur or prices[-1].close),
                    'atr': float(last_row['atr']) if not np.isnan(last_row['atr']) else None,
                    'rsi': float(last_row['rsi']) if not np.isnan(last_row['rsi']) else None,
                    'macd': float(last_row['macd']) if not np.isnan(last_row['macd']) else None,
                    'ema_fast': float(last_row['ema_fast']) if not np.isnan(last_row['ema_fast']) else None,
                    'ema_slow': float(last_row['ema_slow']) if not np.isnan(last_row['ema_slow']) else None,
                    'technical_score': technical_score,
                    'analyst_score': analyst_score,
                    'news_score': news_score,
                    'sector_score': sector_score,
                    'risk_score': None,
                    'params': params,
                    'reason': _build_reason(last_row, params, score, analyst_score, sector_score),
                    'reason_json': {
                        'technical': {
                            'rsi': float(last_row['rsi']) if not np.isnan(last_row['rsi']) else None,
                            'macd': float(last_row['macd']) if not np.isnan(last_row['macd']) else None,
                            'ema_fast': float(last_row['ema_fast']) if not np.isnan(last_row['ema_fast']) else None,
                            'ema_slow': float(last_row['ema_slow']) if not np.isnan(last_row['ema_slow']) else None,
                        },
                        'scores': {
                            'final': score,
                            'technical': technical_score,
                            'analyst': analyst_score,
                            'news': news_score,
                            'sector': sector_score,
                        }
                    },
                    'risk_json': {},
                    'data_snapshot_json': {
                        'as_of_date': as_of_date.isoformat(),
                        'history_points': len(prices),
                    },
                })

            except Exception as e:
                log.error(f"Signal-Berechnung für {stock.symbol} fehlgeschlagen: {e}")

        db.session.commit()

    signals.sort(key=lambda s: s['score'], reverse=True)
    return signals


def generate_signals(app) -> list[dict]:
    """
    Berechnet für alle aktiven Aktien den aktuellen Signal-Score.
    Gibt sortierte Liste nach Score zurück.
    """
    return generate_signals_for_date(app, date.today())


def run_optimization_for_all(app):
    """
    Backtesting & Parameter-Optimierung für alle Aktien.
    Wird beim Start und wöchentlich ausgeführt.
    """
    from models import db, Stock, Price, AlgoParams
    from datetime import timezone as tz

    with app.app_context():
        stocks = Stock.query.filter_by(active=True).all()
        log.info(f"Starte Optimierung für {len(stocks)} Aktien...")

        for stock in stocks:
            try:
                prices = (Price.query
                          .filter_by(stock_id=stock.id)
                          .order_by(Price.date.asc())
                          .all())

                if len(prices) < 100:
                    continue

                df = pd.DataFrame([{
                    'Open': p.open, 'High': p.high, 'Low': p.low,
                    'Close': p.close, 'Volume': p.volume
                } for p in prices], index=[p.date for p in prices])

                best = optimize_parameters(df)
                backtest = best.pop('_backtest', {})

                algo = AlgoParams.query.filter_by(stock_id=stock.id).first()
                if not algo:
                    algo = AlgoParams(stock_id=stock.id)
                    db.session.add(algo)

                algo.rsi_period = best['rsi_period']
                algo.rsi_oversold = best['rsi_oversold']
                algo.rsi_overbought = best['rsi_overbought']
                algo.ema_fast = best['ema_fast']
                algo.ema_slow = best['ema_slow']
                algo.macd_fast = best['macd_fast']
                algo.macd_slow = best['macd_slow']
                algo.macd_signal = best['macd_signal']
                algo.bb_period = best['bb_period']
                algo.bb_std = best['bb_std']
                algo.sharpe_ratio = backtest.get('sharpe', 0.0)
                algo.backtest_return = backtest.get('total_return', 0.0)
                algo.optimized_at = datetime.now(tz.utc)

                db.session.commit()
                log.info(f"{stock.symbol}: Sharpe={algo.sharpe_ratio:.2f}, "
                         f"Return={algo.backtest_return:.1f}%")

            except Exception as e:
                log.error(f"Optimierung für {stock.symbol}: {e}")

        log.info("Optimierung abgeschlossen.")


def _build_reason(row, params, score, analyst_score, sector_score) -> str:
    """Menschenlesbare Begründung für das Signal"""
    parts = []
    rsi = row.get('rsi')
    if rsi is not None and not np.isnan(rsi):
        if rsi < params.get('rsi_oversold', 35):
            parts.append(f"RSI überverkauft ({rsi:.0f})")
        elif rsi > params.get('rsi_overbought', 65):
            parts.append(f"RSI überkauft ({rsi:.0f})")
    if row.get('macd', 0) > row.get('macd_signal', 0):
        parts.append("MACD bullisch")
    elif row.get('macd', 0) < row.get('macd_signal', 0):
        parts.append("MACD bärisch")
    if row.get('ema_fast', 0) > row.get('ema_slow', 0):
        parts.append("EMA-Aufwärtstrend")
    else:
        parts.append("EMA-Abwärtstrend")
    if analyst_score >= 75:
        parts.append("starke Analystenempfehlung")
    if sector_score >= 70:
        parts.append("starkes Sektormomentum")
    return ", ".join(parts) if parts else f"Score: {score:.0f}"


# Import am Ende um zirkuläre Abhängigkeiten zu vermeiden
from datetime import datetime
