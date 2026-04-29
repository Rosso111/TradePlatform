"""
Live Runner — führt den Handelszyklus mit echten IBKR-Orders aus.
Ersetzt run_trading_cycle() für den Live/Paper-Trading-Modus.
"""
import logging
from datetime import date

from models import db, Account, Position, Trade, Stock, Price, EquityHistory
import config
from services.ibkr_connector import connector
from services.trading_engine import (
    calc_commission, calc_spread_cost, calc_stop_loss,
    calc_take_profit, get_open_positions_count,
    get_sector_position_count, already_in_position, update_equity,
)

log = logging.getLogger(__name__)


# ── Kauf via IBKR ─────────────────────────────────────────────────────────────

def _calc_shares(signal: dict, account: Account) -> int:
    """Berechnet ganzzahlige Stückzahl für IBKR (kein Bruchteil)."""
    equity = account.equity_eur
    entry_eur = signal['current_price_eur']
    atr = signal.get('atr')

    if atr and atr > 0 and entry_eur > 0:
        atr_eur = (atr / signal['current_price']) * entry_eur
        risk_per_share = config.ATR_STOP_MULTIPLIER * atr_eur
        risk_amount = equity * config.RISK_PER_TRADE
        size_by_risk = (risk_amount / risk_per_share) * entry_eur
    else:
        size_by_risk = equity * config.RISK_PER_TRADE / config.DEFAULT_STOP_LOSS_PCT

    score_factor = (signal['score'] - 65) / 35
    size_adjusted = size_by_risk * (1 + score_factor * 0.5)

    max_size = equity * config.MAX_POSITION_SIZE
    min_size = equity * config.MIN_POSITION_SIZE
    position_eur = min(max(size_adjusted, min_size), max_size)
    position_eur = min(position_eur, account.cash_eur * 0.98)

    shares = int(position_eur / entry_eur)
    return max(shares, 0)


def execute_live_buy(signal: dict, fx_rates: dict) -> tuple[bool, str]:
    """Kauft via IBKR und trägt die Position in die DB ein."""
    account = Account.query.first()
    symbol  = signal['symbol']
    stock_id = signal['stock_id']

    # Portfolio-Checks
    if get_open_positions_count() >= config.MAX_POSITIONS:
        return False, f"{symbol}: Portfolio voll"
    if get_sector_position_count(signal['sector']) >= config.MAX_POSITIONS_PER_SECTOR:
        return False, f"{symbol}: Sektor voll"
    if already_in_position(stock_id):
        return False, f"{symbol}: Position bereits offen"
    if signal['score'] < config.SIGNAL_THRESHOLD_BUY:
        return False, f"{symbol}: Score zu niedrig"

    shares = _calc_shares(signal, account)
    if shares <= 0:
        return False, f"{symbol}: Stückzahl 0 (zu wenig Kapital)"

    # Vorläufige Kostenprüfung mit aktuellem Schätzpreis
    est_cost = shares * signal['current_price_eur'] * 1.002
    if est_cost > account.cash_eur:
        return False, f"{symbol}: Nicht genug Kapital"

    # ── IBKR Order ────────────────────────────────────────────────────────────
    try:
        fill_price_usd, fill_qty = connector.place_market_order(symbol, shares, 'BUY')
    except Exception as e:
        return False, f"{symbol}: IBKR Order fehlgeschlagen — {e}"

    currency = signal['currency']
    fx_rate  = fx_rates.get(currency, 1.0)
    fill_price_eur = fill_price_usd / fx_rate if currency != 'EUR' else fill_price_usd

    position_eur = fill_qty * fill_price_eur
    commission   = calc_commission(position_eur)
    spread       = calc_spread_cost(position_eur)
    total_cost   = position_eur + commission + spread

    atr = signal.get('atr')
    stop_loss   = calc_stop_loss(fill_price_usd, atr)
    take_profit = calc_take_profit(fill_price_usd, stop_loss)

    # ── DB-Eintrag ────────────────────────────────────────────────────────────
    pos = Position(
        stock_id=stock_id,
        shares=float(fill_qty),
        entry_price=fill_price_usd,
        entry_price_eur=fill_price_eur,
        entry_rate=fx_rate,
        current_price=fill_price_usd,
        current_price_eur=fill_price_eur,
        stop_loss=stop_loss,
        take_profit=take_profit,
        trailing_stop=stop_loss,
        highest_price=fill_price_usd,
        cost_eur=total_cost,
        commission_eur=commission,
        reason=signal.get('reason', '') + ' [IBKR LIVE]',
    )
    db.session.add(pos)

    trade = Trade(
        stock_id=stock_id,
        action='BUY',
        shares=float(fill_qty),
        price=fill_price_usd,
        price_eur=fill_price_eur,
        fx_rate=fx_rate,
        commission_eur=commission,
        total_eur=total_cost,
        pnl_eur=0.0,
        reason=f"IBKR Fill @ ${fill_price_usd:.2f}",
    )
    db.session.add(trade)

    account.cash_eur      -= total_cost
    account.total_trades  += 1
    account.total_commission += commission
    db.session.commit()

    msg = (f"IBKR KAUF {symbol}: {fill_qty} Stück @ ${fill_price_usd:.2f} "
           f"(= {fill_price_eur:.4f} EUR), Kosten: {total_cost:.2f} EUR")
    log.info(msg)
    return True, msg


# ── Verkauf via IBKR ──────────────────────────────────────────────────────────

def execute_live_sell(position: Position, fx_rates: dict, reason: str) -> tuple[bool, str]:
    """Verkauft via IBKR und schließt die Position in der DB."""
    symbol   = position.stock.symbol
    qty      = int(position.shares)
    currency = position.stock.currency
    fx_rate  = fx_rates.get(currency, 1.0)

    if qty <= 0:
        return False, f"{symbol}: Stückzahl 0"

    try:
        fill_price_usd, _ = connector.place_market_order(symbol, qty, 'SELL')
    except Exception as e:
        return False, f"{symbol}: IBKR Sell-Order fehlgeschlagen — {e}"

    fill_price_eur = fill_price_usd / fx_rate if currency != 'EUR' else fill_price_usd

    revenue    = qty * fill_price_eur
    commission = calc_commission(revenue)
    spread     = calc_spread_cost(revenue)
    net_revenue = revenue - commission - spread

    cost_basis = position.shares * position.entry_price_eur
    pnl_eur    = net_revenue - cost_basis
    pnl_pct    = (pnl_eur / cost_basis * 100) if cost_basis > 0 else 0

    trade = Trade(
        stock_id=position.stock_id,
        action='SELL',
        shares=float(qty),
        price=fill_price_usd,
        price_eur=fill_price_eur,
        fx_rate=fx_rate,
        commission_eur=commission,
        total_eur=net_revenue,
        pnl_eur=pnl_eur,
        pnl_pct=pnl_pct,
        reason=f"IBKR Fill @ ${fill_price_usd:.2f} — {reason}",
    )
    db.session.add(trade)

    account = Account.query.first()
    account.cash_eur      += net_revenue
    account.total_trades  += 1
    account.total_commission += commission
    if pnl_eur > 0:
        account.winning_trades += 1

    db.session.delete(position)
    db.session.commit()

    msg = (f"IBKR VERKAUF {symbol}: {qty} Stück @ ${fill_price_usd:.2f}, "
           f"P&L: {pnl_eur:+.2f} EUR ({pnl_pct:+.1f}%), Grund: {reason}")
    log.info(msg)
    return True, msg


# ── Positionen überwachen (SL/TP via IBKR) ───────────────────────────────────

def update_live_positions(fx_rates: dict) -> list[str]:
    """Prüft SL/TP für alle offenen Positionen und sendet ggf. Sell-Orders."""
    actions = []
    for pos in Position.query.all():
        stock    = pos.stock
        currency = stock.currency
        fx_rate  = fx_rates.get(currency, 1.0)

        latest = (Price.query
                  .filter_by(stock_id=stock.id)
                  .order_by(Price.date.desc())
                  .first())
        if not latest:
            continue

        current_price     = latest.close
        current_price_eur = latest.close_eur or (current_price / fx_rate)

        pos.current_price     = current_price
        pos.current_price_eur = current_price_eur

        # Trailing-Stop nachziehen
        if current_price > (pos.highest_price or pos.entry_price):
            pos.highest_price = current_price
            new_trailing = current_price * (1 - config.TRAILING_STOP_PCT)
            if new_trailing > (pos.trailing_stop or 0):
                pos.trailing_stop = new_trailing

        effective_stop = max(pos.stop_loss or 0, pos.trailing_stop or 0)

        if effective_stop > 0 and current_price <= effective_stop:
            ok, msg = execute_live_sell(pos, fx_rates, reason='Stop-Loss ausgelöst')
            actions.append(msg)
        elif pos.take_profit and current_price >= pos.take_profit:
            ok, msg = execute_live_sell(pos, fx_rates, reason='Take-Profit erreicht')
            actions.append(msg)

    db.session.commit()
    return actions


# ── Haupt-Zyklus ──────────────────────────────────────────────────────────────

def run_live_trading_cycle(app) -> list[str]:
    """
    Vollständiger Live-Handelszyklus mit IBKR-Ausführung.
    Gleiche Schnittstelle wie run_trading_cycle() in trading_engine.py.
    """
    from services.data_fetcher import fetch_exchange_rates, update_prices_incremental
    from services.algorithm import generate_signals

    log.info("=== IBKR Live-Zyklus gestartet ===")
    actions = []

    if not connector.ensure_connected():
        log.error("IBKR nicht verbunden — Live-Zyklus abgebrochen")
        return ["FEHLER: IBKR Gateway nicht erreichbar"]

    with app.app_context():
        # 1. Wechselkurse
        try:
            fx_rates = fetch_exchange_rates()
        except Exception as e:
            log.error(f"Wechselkurse: {e}")
            fx_rates = {'USD': 1.08, 'GBP': 0.85, 'JPY': 163.0,
                        'CHF': 0.96, 'HKD': 8.45, 'EUR': 1.0}

        # 2. Preise aktualisieren
        try:
            update_prices_incremental(app, config.STOCK_UNIVERSE)
        except Exception as e:
            log.error(f"Preis-Update: {e}")

        # 3. SL/TP via IBKR prüfen
        try:
            actions.extend(update_live_positions(fx_rates))
        except Exception as e:
            log.error(f"Positions-Update: {e}")

        # 4. Signale generieren
        try:
            signals = generate_signals(app)
        except Exception as e:
            log.error(f"Signal-Generierung: {e}")
            signals = []

        # 5. Kaufen
        buy_signals = [s for s in signals if s['action'] == 'BUY']
        for signal in sorted(buy_signals, key=lambda s: s['score'], reverse=True):
            if get_open_positions_count() >= config.MAX_POSITIONS:
                break
            try:
                ok, msg = execute_live_buy(signal, fx_rates)
                if ok:
                    actions.append(msg)
            except Exception as e:
                log.error(f"Live-Kauf {signal['symbol']}: {e}")

        # 6. Verkaufen (Signal)
        sell_signals = {s['stock_id']: s for s in signals if s['action'] == 'SELL'}
        for pos in Position.query.all():
            if pos.stock_id in sell_signals:
                sig = sell_signals[pos.stock_id]
                try:
                    ok, msg = execute_live_sell(
                        pos, fx_rates,
                        reason=f"Verkaufssignal (Score {sig['score']:.0f})"
                    )
                    if ok:
                        actions.append(msg)
                except Exception as e:
                    log.error(f"Live-Verkauf {pos.stock.symbol}: {e}")

        # 7. Equity
        try:
            update_equity(app)
        except Exception as e:
            log.error(f"Equity-Update: {e}")

    log.info(f"=== IBKR Live-Zyklus beendet: {len(actions)} Aktionen ===")
    return actions
