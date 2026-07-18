"""Live-Signalgenerator für Dual-Momentum-Strategien (Antonacci).

Portiert die Replay-Logik (replay_engine, strategy_mode 'dual_momentum') in den
Sim-/Live-Pfad: absolutes Momentum (Return > Schwelle) + relatives Momentum
(Top-N-Ranking). Es gibt keinen Score-Algorithmus — das Ranking ist das Signal;
'score' dient nur der Sortierung und den bestehenden Schwellen-Checks.
"""
import logging

from models import Price, Stock

log = logging.getLogger(__name__)


def _atr14(rows) -> float | None:
    """ATR(14) aus absteigend sortierten Price-Zeilen (neueste zuerst)."""
    if len(rows) < 15:
        return None
    trs = []
    for i in range(14):
        cur, prev = rows[i], rows[i + 1]
        if cur.high is None or cur.low is None or prev.close is None:
            return None
        trs.append(max(cur.high - cur.low,
                       abs(cur.high - prev.close),
                       abs(cur.low - prev.close)))
    return sum(trs) / len(trs)


def generate_momentum_signals(portfolio) -> list[dict]:
    """Signale für ein dual_momentum-Portfolio (muss im App-Context laufen)."""
    from services.strategy_resolver import resolve

    params = resolve(portfolio)
    lookback = int(params.get('momentum_lookback_days', 252))
    abs_thr = float(params.get('absolute_momentum_threshold', 0.0))
    top_n = int(params.get('top_n_signals', 10))

    stock_query = Stock.query.filter(Stock.active.is_(True))
    if params.get('signal_universe', 'stock_universe') == 'stock_universe':
        # Nur live handelbare Titel — DB enthält auch Alt-Importe (z. B. .KS),
        # die weder handelbar sind noch laufende Kurs-Updates bekommen
        import config
        stock_query = stock_query.filter(
            Stock.symbol.in_({s['symbol'] for s in config.STOCK_UNIVERSE}))
    excluded_regions = set(params.get('signal_exclude_regions') or [])
    if excluded_regions:
        # z. B. KR/JP/CN: im Universum, aber IBKR füllt dort keine Orders
        stock_query = stock_query.filter(~Stock.region.in_(excluded_regions))

    momentum: dict[int, float] = {}
    latest_by_stock: dict[int, tuple] = {}
    stock_by_id: dict[int, Stock] = {}
    for stock in stock_query.all():
        rows = (Price.query.filter_by(stock_id=stock.id)
                .order_by(Price.date.desc())
                .limit(lookback + 1)
                .all())
        if len(rows) < lookback + 1:
            continue
        latest, past = rows[0], rows[-1]
        if not latest.close or not past.close:
            continue
        momentum[stock.id] = latest.close / past.close - 1
        latest_by_stock[stock.id] = (latest, _atr14(rows))
        stock_by_id[stock.id] = stock

    eligible = [sid for sid, m in momentum.items() if m > abs_thr]
    ranked = sorted(eligible, key=lambda sid: momentum[sid], reverse=True)
    top_ids = set(ranked[:top_n])

    signals = []
    for sid, mom in momentum.items():
        stock = stock_by_id[sid]
        latest, atr = latest_by_stock[sid]
        if sid in top_ids:
            action = 'BUY'
            rank = ranked.index(sid) + 1
        elif mom < 0:
            action, rank = 'SELL', 999
        else:
            action, rank = 'HOLD', 999
        signals.append({
            'stock_id': sid,
            'symbol': stock.symbol,
            'name': stock.name,
            'sector': stock.sector,
            'currency': stock.currency,
            'action': action,
            'score': min(50 + mom * 50, 100),
            'dm_rank': rank,
            'current_price': latest.close,
            'current_price_eur': latest.close_eur or latest.close,
            'atr': atr,
            'reason': f'Dual Momentum: {lookback}T-Return {mom * 100:+.0f}%'
                      + (f', Rang {rank}' if rank != 999 else ''),
        })

    signals.sort(key=lambda s: s['score'], reverse=True)
    log.info("Momentum-Signale Portfolio %s: %d BUY (Top-%d), %d SELL, %d Aktien mit Historie",
             portfolio.id, sum(1 for s in signals if s['action'] == 'BUY'), top_n,
             sum(1 for s in signals if s['action'] == 'SELL'), len(momentum))
    return signals
