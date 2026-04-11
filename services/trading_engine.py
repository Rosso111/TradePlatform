"""
Trading Engine
Trifft autonome Kauf-/Verkaufsentscheidungen, verwaltet Positionen,
berechnet Handelskosten und aktualisiert den Kontostand.
"""
import logging
from datetime import date, datetime, timezone

import config
from models import db, Account, Position, Trade, Stock, Price, EquityHistory

log = logging.getLogger(__name__)


# ─── Handelskosten ───────────────────────────────────────────────────────────

def calc_commission(value_eur: float) -> float:
    """Provision: 0.1% des Handelswertes, mind. 1 EUR"""
    commission = value_eur * config.COMMISSION_RATE
    return max(commission, config.MIN_COMMISSION)


def calc_spread_cost(value_eur: float) -> float:
    """Spread-Kosten: 0.05% pro Seite"""
    return value_eur * config.SPREAD_RATE


def total_trade_cost(value_eur: float) -> float:
    """Gesamtkosten eines Trades (Provision + Spread)"""
    return calc_commission(value_eur) + calc_spread_cost(value_eur)


# ─── Stop-Loss & Take-Profit Berechnung ──────────────────────────────────────

def calc_stop_loss(entry_price: float, atr: float | None) -> float:
    """
    ATR-basierter Stop-Loss: Einstieg - (ATR_MULTIPLIER × ATR)
    Fallback: DEFAULT_STOP_LOSS_PCT vom Einstiegspreis
    """
    if atr and atr > 0:
        stop = entry_price - config.ATR_STOP_MULTIPLIER * atr
    else:
        stop = entry_price * (1 - config.DEFAULT_STOP_LOSS_PCT)
    # Mindest-Stop: nie mehr als MAX_STOP% unter Einstieg
    min_stop = entry_price * (1 - config.DEFAULT_STOP_LOSS_PCT * 1.5)
    return max(stop, min_stop)


def calc_take_profit(entry_price: float, stop_loss: float) -> float:
    """
    Take-Profit mit Risk/Reward >= 2:1
    """
    risk = entry_price - stop_loss
    return entry_price + max(risk * 2.5, entry_price * config.DEFAULT_TAKE_PROFIT_PCT)


def calc_position_size(account: Account, signal: dict) -> float:
    """
    Positionsgröße in EUR basierend auf:
    - 2% Kapital-Risiko pro Trade
    - Maximale / minimale Positionsgröße
    - Verfügbarem Cash
    """
    equity = account.equity_eur
    entry_eur = signal['current_price_eur']
    atr = signal.get('atr')

    # ATR-basiertes Risiko
    if atr and atr > 0 and entry_eur > 0:
        atr_eur = (atr / signal['current_price']) * entry_eur
        risk_per_share = config.ATR_STOP_MULTIPLIER * atr_eur
        risk_amount = equity * config.RISK_PER_TRADE
        size_by_risk = risk_amount / risk_per_share * entry_eur
    else:
        size_by_risk = equity * config.RISK_PER_TRADE / config.DEFAULT_STOP_LOSS_PCT

    # Score-gewichtete Größe: höherer Score → größere Position
    score_factor = (signal['score'] - 65) / 35  # 0.0 bis 1.0
    size_adjusted = size_by_risk * (1 + score_factor * 0.5)

    # Grenzen einhalten
    max_size = equity * config.MAX_POSITION_SIZE
    min_size = equity * config.MIN_POSITION_SIZE
    size = min(max(size_adjusted, min_size), max_size)

    # Nicht mehr als verfügbares Cash
    return min(size, account.cash_eur * 0.98)


# ─── Portfolio-Prüfungen ─────────────────────────────────────────────────────

def get_open_positions_count() -> int:
    return Position.query.count()


def get_sector_position_count(sector: str) -> int:
    return (Position.query
            .join(Stock)
            .filter(Stock.sector == sector)
            .count())


def already_in_position(stock_id: int) -> bool:
    return Position.query.filter_by(stock_id=stock_id).first() is not None


# ─── Kauf-Ausführung ─────────────────────────────────────────────────────────

def execute_buy(signal: dict, fx_rates: dict) -> tuple[bool, str]:
    """
    Kauft eine Position wenn alle Bedingungen erfüllt sind.
    Gibt (Erfolg, Meldung) zurück.
    """
    account = Account.query.first()
    stock_id = signal['stock_id']
    symbol = signal['symbol']

    # Bedingungen prüfen
    if get_open_positions_count() >= config.MAX_POSITIONS:
        return False, f"{symbol}: Portfolio voll ({config.MAX_POSITIONS} Positionen)"

    if get_sector_position_count(signal['sector']) >= config.MAX_POSITIONS_PER_SECTOR:
        return False, f"{symbol}: Sektor {signal['sector']} voll ({config.MAX_POSITIONS_PER_SECTOR} Pos.)"

    if already_in_position(stock_id):
        return False, f"{symbol}: Position bereits offen"

    if signal['score'] < config.SIGNAL_THRESHOLD_BUY:
        return False, f"{symbol}: Score {signal['score']:.0f} unter Schwelle {config.SIGNAL_THRESHOLD_BUY}"

    # Positionsgröße berechnen
    position_eur = calc_position_size(account, signal)
    if position_eur < 50:
        return False, f"{symbol}: Positionsgröße zu klein ({position_eur:.2f} EUR)"

    entry_price = signal['current_price']
    entry_price_eur = signal['current_price_eur']
    currency = signal['currency']
    fx_rate = fx_rates.get(currency, 1.0)

    # Handelskosten
    commission = calc_commission(position_eur)
    spread = calc_spread_cost(position_eur)
    total_cost = position_eur + commission + spread

    if total_cost > account.cash_eur:
        return False, f"{symbol}: Nicht genug Kapital ({account.cash_eur:.2f} EUR < {total_cost:.2f} EUR)"

    # Anzahl Aktien (inkl. Spread auf Einstiegspreis)
    entry_price_with_spread = entry_price * (1 + config.SPREAD_RATE)
    entry_eur_with_spread = entry_price_eur * (1 + config.SPREAD_RATE)
    shares = position_eur / entry_eur_with_spread

    # Stop-Loss & Take-Profit
    atr = signal.get('atr')
    stop_loss = calc_stop_loss(entry_price, atr)
    take_profit = calc_take_profit(entry_price, stop_loss)

    # Position anlegen
    pos = Position(
        stock_id=stock_id,
        shares=shares,
        entry_price=entry_price_with_spread,
        entry_price_eur=entry_eur_with_spread,
        entry_rate=fx_rate,
        current_price=entry_price,
        current_price_eur=entry_price_eur,
        stop_loss=stop_loss,
        take_profit=take_profit,
        trailing_stop=stop_loss,
        highest_price=entry_price,
        cost_eur=total_cost,
        commission_eur=commission,
        reason=signal.get('reason', ''),
    )
    db.session.add(pos)

    # Trade-Log
    trade = Trade(
        stock_id=stock_id,
        action='BUY',
        shares=shares,
        price=entry_price_with_spread,
        price_eur=entry_eur_with_spread,
        fx_rate=fx_rate,
        commission_eur=commission,
        total_eur=total_cost,
        pnl_eur=0.0,
        reason=signal.get('reason', ''),
    )
    db.session.add(trade)

    # Kontostand aktualisieren
    account.cash_eur -= total_cost
    account.total_trades += 1
    account.total_commission += commission

    db.session.commit()

    msg = (f"KAUF {symbol}: {shares:.2f} Aktien @ {entry_price_eur:.4f} EUR "
           f"(SL: {stop_loss:.4f}, TP: {take_profit:.4f}), "
           f"Kosten: {total_cost:.2f} EUR")
    log.info(msg)
    return True, msg


# ─── Verkauf-Ausführung ──────────────────────────────────────────────────────

def execute_sell(position: Position, current_price: float,
                 current_price_eur: float, fx_rate: float,
                 reason: str) -> tuple[bool, str]:
    """Schließt eine offene Position."""
    revenue = position.shares * current_price_eur
    commission = calc_commission(revenue)
    spread = calc_spread_cost(revenue)
    net_revenue = revenue - commission - spread

    # Realisierter Gewinn/Verlust
    cost_basis = position.shares * position.entry_price_eur
    pnl_eur = net_revenue - cost_basis
    pnl_pct = (pnl_eur / cost_basis * 100) if cost_basis > 0 else 0

    # Trade-Log
    trade = Trade(
        stock_id=position.stock_id,
        action='SELL',
        shares=position.shares,
        price=current_price,
        price_eur=current_price_eur,
        fx_rate=fx_rate,
        commission_eur=commission,
        total_eur=net_revenue,
        pnl_eur=pnl_eur,
        pnl_pct=pnl_pct,
        reason=reason,
    )
    db.session.add(trade)

    # Kontostand
    account = Account.query.first()
    account.cash_eur += net_revenue
    account.total_trades += 1
    account.total_commission += commission
    if pnl_eur > 0:
        account.winning_trades += 1

    db.session.delete(position)
    db.session.commit()

    symbol = position.stock.symbol
    msg = (f"VERKAUF {symbol}: {position.shares:.2f} Aktien @ {current_price_eur:.4f} EUR, "
           f"P&L: {pnl_eur:+.2f} EUR ({pnl_pct:+.1f}%), Grund: {reason}")
    log.info(msg)
    return True, msg


# ─── Positionen aktualisieren ────────────────────────────────────────────────

def update_positions(fx_rates: dict) -> list[str]:
    """
    Aktualisiert Preise aller offenen Positionen,
    prüft Stop-Loss / Take-Profit, aktualisiert Trailing-Stop.
    Gibt Liste von Aktionen zurück.
    """
    actions = []
    positions = Position.query.all()

    for pos in positions:
        stock = pos.stock
        currency = stock.currency
        fx_rate = fx_rates.get(currency, 1.0)

        # Letzten Preis aus DB holen
        latest = (Price.query
                  .filter_by(stock_id=stock.id)
                  .order_by(Price.date.desc())
                  .first())
        if not latest:
            continue

        current_price = latest.close
        current_price_eur = latest.close_eur or (current_price / fx_rate if fx_rate > 0 else current_price)

        pos.current_price = current_price
        pos.current_price_eur = current_price_eur

        # Trailing-Stop nachziehen
        if current_price > (pos.highest_price or pos.entry_price):
            pos.highest_price = current_price
            new_trailing = current_price * (1 - config.TRAILING_STOP_PCT)
            if new_trailing > (pos.trailing_stop or 0):
                pos.trailing_stop = new_trailing

        effective_stop = max(
            pos.stop_loss or 0,
            pos.trailing_stop or 0
        )

        # Stop-Loss getroffen?
        if effective_stop > 0 and current_price <= effective_stop:
            ok, msg = execute_sell(pos, current_price, current_price_eur, fx_rate,
                                   reason='Stop-Loss ausgelöst')
            actions.append(msg)
            continue

        # Take-Profit getroffen?
        if pos.take_profit and current_price >= pos.take_profit:
            ok, msg = execute_sell(pos, current_price, current_price_eur, fx_rate,
                                   reason='Take-Profit erreicht')
            actions.append(msg)
            continue

    db.session.commit()
    return actions


# ─── Haupt-Trading-Schleife ──────────────────────────────────────────────────

def run_trading_cycle(app) -> list[str]:
    """
    Vollständiger Handelszyklus:
    1. Wechselkurse laden
    2. Preise aktualisieren
    3. Stop-Loss / Take-Profit prüfen
    4. Signale berechnen
    5. Kaufentscheidungen treffen
    6. Equity-Stand aktualisieren
    """
    from services.data_fetcher import fetch_exchange_rates, update_prices_incremental
    from services.algorithm import generate_signals

    log.info("=== Handelszyklus gestartet ===")
    actions = []

    with app.app_context():
        # 1. Wechselkurse aktualisieren
        try:
            fx_rates = fetch_exchange_rates()
        except Exception as e:
            log.error(f"Wechselkurse: {e}")
            fx_rates = {'USD': 1.08, 'GBP': 0.85, 'JPY': 163.0,
                        'CHF': 0.96, 'HKD': 8.45, 'KRW': 1450.0, 'AUD': 1.65, 'EUR': 1.0}

        # 2. Preise inkrementell aktualisieren
        try:
            update_prices_incremental(app, config.STOCK_UNIVERSE)
        except Exception as e:
            log.error(f"Preis-Update: {e}")

        # 3. Offene Positionen prüfen (Stop-Loss / Take-Profit)
        try:
            sl_actions = update_positions(fx_rates)
            actions.extend(sl_actions)
        except Exception as e:
            log.error(f"Positions-Update: {e}")

        # 4. Signale generieren
        try:
            signals = generate_signals(app)
        except Exception as e:
            log.error(f"Signal-Generierung: {e}")
            signals = []

        # 5. Kaufentscheidungen
        buy_signals = [s for s in signals if s['action'] == 'BUY']
        for signal in buy_signals:
            if get_open_positions_count() >= config.MAX_POSITIONS:
                break
            try:
                ok, msg = execute_buy(signal, fx_rates)
                if ok:
                    actions.append(msg)
            except Exception as e:
                log.error(f"Kauf {signal['symbol']}: {e}")

        # 6. Verkaufssignale für bestehende Positionen
        sell_signals = {s['stock_id']: s for s in signals if s['action'] == 'SELL'}
        for pos in Position.query.all():
            if pos.stock_id in sell_signals:
                sig = sell_signals[pos.stock_id]
                currency = pos.stock.currency
                fx_rate = fx_rates.get(currency, 1.0)
                try:
                    ok, msg = execute_sell(
                        pos,
                        sig['current_price'],
                        sig['current_price_eur'],
                        fx_rate,
                        reason=f"Verkaufssignal (Score {sig['score']:.0f})"
                    )
                    if ok:
                        actions.append(msg)
                except Exception as e:
                    log.error(f"Verkauf {pos.stock.symbol}: {e}")

        # 7. Equity aktualisieren
        try:
            update_equity(app)
        except Exception as e:
            log.error(f"Equity-Update: {e}")

    log.info(f"=== Handelszyklus beendet: {len(actions)} Aktionen ===")
    return actions


def update_equity(app):
    """Berechnet und speichert den aktuellen Gesamtwert des Portfolios."""
    account = Account.query.first()
    positions = Position.query.all()

    positions_value = sum(
        (p.current_price_eur or p.entry_price_eur) * p.shares
        for p in positions
    )
    equity = account.cash_eur + positions_value
    account.equity_eur = equity

    today = date.today()
    history = EquityHistory.query.filter_by(date=today).first()

    yesterday = (EquityHistory.query
                 .filter(EquityHistory.date < today)
                 .order_by(EquityHistory.date.desc())
                 .first())

    daily_pnl = equity - (yesterday.equity_eur if yesterday else config.STARTING_CAPITAL)

    if not history:
        history = EquityHistory(date=today)
        db.session.add(history)
    history.equity_eur = equity
    history.cash_eur = account.cash_eur
    history.positions_value = positions_value
    history.daily_pnl = daily_pnl

    db.session.commit()
