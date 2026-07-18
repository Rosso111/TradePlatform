"""
Trading Engine
Trifft autonome Kauf-/Verkaufsentscheidungen, verwaltet Positionen,
berechnet Handelskosten und aktualisiert den Kontostand.
"""
import logging
from datetime import date, datetime, timezone

import config
from models import db, Account, Position, Trade, Stock, Price, EquityHistory, Portfolio

log = logging.getLogger(__name__)


# ─── Handelskosten ───────────────────────────────────────────────────────────

def calc_commission(value_eur: float, params: dict | None = None) -> float:
    p = params or {}
    rate  = p.get('commission_rate', config.COMMISSION_RATE)
    min_c = p.get('min_commission',  config.MIN_COMMISSION)
    return max(value_eur * rate, min_c)


def calc_spread_cost(value_eur: float, params: dict | None = None) -> float:
    p = params or {}
    return value_eur * p.get('spread_rate', config.SPREAD_RATE)


def total_trade_cost(value_eur: float, params: dict | None = None) -> float:
    return calc_commission(value_eur, params) + calc_spread_cost(value_eur, params)


# ─── Stop-Loss & Take-Profit Berechnung ──────────────────────────────────────

def calc_stop_loss(entry_price: float, atr: float | None, params: dict | None = None) -> float:
    p      = params or {}
    mult   = p.get('atr_stop_multiplier',   config.ATR_STOP_MULTIPLIER)
    sl_pct = p.get('default_stop_loss_pct', config.DEFAULT_STOP_LOSS_PCT)
    if atr and atr > 0:
        stop = entry_price - mult * atr
    else:
        stop = entry_price * (1 - sl_pct)
    min_stop = entry_price * (1 - sl_pct * 1.5)
    return max(stop, min_stop)


def calc_take_profit(entry_price: float, stop_loss: float, params: dict | None = None) -> float:
    p      = params or {}
    tp_pct = p.get('default_take_profit_pct', config.DEFAULT_TAKE_PROFIT_PCT)
    risk   = entry_price - stop_loss
    return entry_price + max(risk * 2.5, entry_price * tp_pct)


def calc_position_size(account: Account, signal: dict, params: dict | None = None) -> float:
    p         = params or {}
    equity    = account.equity_eur
    entry_eur = signal['current_price_eur']
    atr       = signal.get('atr')
    mult      = p.get('atr_stop_multiplier',    config.ATR_STOP_MULTIPLIER)
    risk_pct  = p.get('risk_per_trade',         config.RISK_PER_TRADE)
    sl_pct    = p.get('default_stop_loss_pct',  config.DEFAULT_STOP_LOSS_PCT)
    max_pct   = p.get('max_position_size',      config.MAX_POSITION_SIZE)
    min_pct   = p.get('min_position_size',      config.MIN_POSITION_SIZE)

    if atr and atr > 0 and entry_eur > 0:
        atr_eur       = (atr / signal['current_price']) * entry_eur
        risk_per_share = mult * atr_eur
        size_by_risk  = (equity * risk_pct / risk_per_share) * entry_eur
    else:
        size_by_risk = equity * risk_pct / sl_pct

    buy_thresh = p.get('buy_threshold', config.SIGNAL_THRESHOLD_BUY)
    score_range = max(1, 100 - buy_thresh)
    score_factor = max(0.0, (signal['score'] - buy_thresh)) / score_range
    size_adjusted = size_by_risk * (0.5 + score_factor * 1.5)  # 0.5x at threshold → 2.0x at score=100

    max_eur  = p.get('max_position_eur', config.MAX_POSITION_EUR)
    max_size = min(equity * max_pct, max_eur)
    min_size = equity * min_pct
    size = min(max(size_adjusted, min_size), max_size)
    return min(size, account.cash_eur * 0.98)


# ─── Portfolio-Prüfungen (portfolio-bewusst) ─────────────────────────────────

def get_open_positions_count(portfolio_id: int) -> int:
    return Position.query.filter_by(portfolio_id=portfolio_id).count()


def get_sector_position_count(sector: str, portfolio_id: int) -> int:
    return (Position.query
            .join(Stock)
            .filter(Stock.sector == sector, Position.portfolio_id == portfolio_id)
            .count())


def already_in_position(stock_id: int, portfolio_id: int) -> bool:
    return Position.query.filter_by(stock_id=stock_id, portfolio_id=portfolio_id).first() is not None


# ─── Kauf-Ausführung ─────────────────────────────────────────────────────────

def execute_buy(signal: dict, fx_rates: dict, portfolio: Portfolio) -> tuple[bool, str]:
    """
    Kauft eine Position wenn alle Bedingungen erfüllt sind.
    Gibt (Erfolg, Meldung) zurück.
    """
    from services.strategy_resolver import resolve

    account = Account.query.filter_by(portfolio_id=portfolio.id).first()
    if not account:
        return False, f"Kein Konto für Portfolio {portfolio.id}"

    stock_id = signal['stock_id']
    symbol   = signal['symbol']
    stock    = Stock.query.get(stock_id)
    params   = resolve(portfolio, stock)

    if get_open_positions_count(portfolio.id) >= params['max_positions']:
        return False, f"{symbol}: Portfolio voll ({params['max_positions']} Positionen)"

    if get_sector_position_count(signal['sector'], portfolio.id) >= params['max_positions_per_sector']:
        return False, f"{symbol}: Sektor {signal['sector']} voll ({params['max_positions_per_sector']} Pos.)"

    if already_in_position(stock_id, portfolio.id):
        return False, f"{symbol}: Position bereits offen"

    if signal['score'] < params['buy_threshold']:
        return False, f"{symbol}: Score {signal['score']:.0f} unter Schwelle {params['buy_threshold']}"

    if params.get('position_sizing') == 'fixed_fraction':
        # Replay-Parität (dual_momentum): fester Cash-Anteil statt Risiko-Sizing,
        # ohne MAX_POSITION_EUR-Cap — Konzentration ist hier gewollt.
        # Kleine Budgets werden auf den Mindestanteil angehoben (Cash-Limit gilt);
        # reicht der Cash nicht für die Mindestposition, kein Kauf.
        min_eur = ((getattr(portfolio, 'starting_capital', None) or config.STARTING_CAPITAL)
                   * float(params.get('min_position_size', 0)))
        position_eur = min(
            max(account.cash_eur * float(params.get('max_position_size', config.MAX_POSITION_SIZE)),
                min_eur),
            account.cash_eur * 0.98,
        )
        if min_eur and position_eur < min_eur:
            return False, f"{symbol}: Zu wenig Cash für Mindestposition ({position_eur:.0f} < {min_eur:.0f} EUR)"
    else:
        position_eur = calc_position_size(account, signal, params)
    if position_eur < 50:
        return False, f"{symbol}: Positionsgröße zu klein ({position_eur:.2f} EUR)"

    entry_price     = signal['current_price']
    entry_price_eur = signal['current_price_eur']
    currency        = signal['currency']
    fx_rate         = fx_rates.get(currency, 1.0)

    commission = calc_commission(position_eur, params)
    spread     = calc_spread_cost(position_eur, params)
    total_cost = position_eur + commission + spread

    if total_cost > account.cash_eur:
        return False, f"{symbol}: Nicht genug Kapital ({account.cash_eur:.2f} EUR < {total_cost:.2f} EUR)"

    spread_rate              = params.get('spread_rate', config.SPREAD_RATE)
    entry_price_with_spread  = entry_price * (1 + spread_rate)
    entry_eur_with_spread    = entry_price_eur * (1 + spread_rate)
    shares = position_eur / entry_eur_with_spread

    atr         = signal.get('atr')
    stop_loss   = calc_stop_loss(entry_price, atr, params)
    take_profit = calc_take_profit(entry_price, stop_loss, params)

    # Position anlegen
    pos = Position(
        portfolio_id=portfolio.id,
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
        portfolio_id=portfolio.id,
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

    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        log.error(f"{symbol}: DB-Commit bei Kauf fehlgeschlagen — {e}")
        return False, f"{symbol}: Datenbankfehler beim Kauf-Eintrag — {e}"

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
    from services.strategy_resolver import resolve
    portfolio = Portfolio.query.get(position.portfolio_id)
    params    = resolve(portfolio, position.stock) if portfolio else None

    # Vor dem Delete sichern: nach dem Commit ist die Instanz detached und
    # Attribut-/Relationship-Zugriffe darauf sind nicht mehr zuverlässig.
    symbol = position.stock.symbol
    shares = position.shares

    revenue    = position.shares * current_price_eur
    commission = calc_commission(revenue, params)
    spread     = calc_spread_cost(revenue, params)
    net_revenue = revenue - commission - spread

    # Realisierter Gewinn/Verlust
    cost_basis = position.shares * position.entry_price_eur
    pnl_eur = net_revenue - cost_basis
    pnl_pct = (pnl_eur / cost_basis * 100) if cost_basis > 0 else 0

    # Trade-Log
    trade = Trade(
        portfolio_id=position.portfolio_id,
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
    account = Account.query.filter_by(portfolio_id=position.portfolio_id).first()
    account.cash_eur += net_revenue
    account.total_trades += 1
    account.total_commission += commission
    if pnl_eur > 0:
        account.winning_trades += 1

    db.session.delete(position)

    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        log.error(f"{symbol}: DB-Commit bei Verkauf fehlgeschlagen — {e}")
        return False, f"{symbol}: Datenbankfehler beim Verkauf-Eintrag — {e}"

    msg = (f"VERKAUF {symbol}: {shares:.2f} Aktien @ {current_price_eur:.4f} EUR, "
           f"P&L: {pnl_eur:+.2f} EUR ({pnl_pct:+.1f}%), Grund: {reason}")
    log.info(msg)
    return True, msg


# ─── Positionen aktualisieren ────────────────────────────────────────────────

def update_positions(fx_rates: dict, portfolio_id: int) -> tuple[list[str], set[int]]:
    """
    Aktualisiert Preise aller offenen Positionen eines Portfolios,
    prüft Stop-Loss / Take-Profit, aktualisiert Trailing-Stop.
    Gibt (actions, sold_stock_ids) zurück — sold_stock_ids verhindert
    Sofort-Wiederkauf im selben Zyklus.
    """
    from services.strategy_resolver import resolve
    actions        = []
    sold_stock_ids: set[int] = set()
    portfolio      = Portfolio.query.get(portfolio_id)
    positions      = Position.query.filter_by(portfolio_id=portfolio_id).all()

    for pos in positions:
        stock    = pos.stock
        currency = stock.currency
        fx_rate  = fx_rates.get(currency, 1.0)
        params   = resolve(portfolio, stock) if portfolio else {}

        latest = (Price.query
                  .filter_by(stock_id=stock.id)
                  .order_by(Price.date.desc())
                  .first())
        if not latest:
            continue

        current_price     = latest.close
        current_price_eur = latest.close_eur or (current_price / fx_rate if fx_rate > 0 else current_price)

        pos.current_price     = current_price
        pos.current_price_eur = current_price_eur

        trailing_pct = params.get('trailing_stop_pct', config.TRAILING_STOP_PCT)
        if current_price > (pos.highest_price or pos.entry_price):
            pos.highest_price = current_price
            new_trailing = current_price * (1 - trailing_pct)
            if new_trailing > (pos.trailing_stop or 0):
                pos.trailing_stop = new_trailing

        effective_stop = max(
            pos.stop_loss or 0,
            pos.trailing_stop or 0
        )

        # Mindesthaltedauer (dual_momentum): Stops/TP erst nach min_hold_days
        # scharf — der Trailing-Stop wird oben trotzdem weiter nachgezogen
        min_hold = int(params.get('min_hold_days', 0) or 0)
        if min_hold and pos.opened_at and (date.today() - pos.opened_at.date()).days < min_hold:
            continue

        # Stop-Loss getroffen?
        if effective_stop > 0 and current_price <= effective_stop:
            ok, msg = execute_sell(pos, current_price, current_price_eur, fx_rate,
                                   reason='Stop-Loss ausgelöst')
            if ok:
                sold_stock_ids.add(pos.stock_id)
            actions.append(msg)
            continue

        # Take-Profit getroffen?
        if pos.take_profit and current_price >= pos.take_profit:
            ok, msg = execute_sell(pos, current_price, current_price_eur, fx_rate,
                                   reason='Take-Profit erreicht')
            if ok:
                sold_stock_ids.add(pos.stock_id)
            actions.append(msg)
            continue

    db.session.commit()
    return actions, sold_stock_ids


# ─── Pro-Portfolio Zyklus ────────────────────────────────────────────────────

def _strategy_mode(portfolio: Portfolio) -> str:
    """Engine-Modus der Portfolio-Strategie ('score', 'dual_momentum', …)."""
    strategy = getattr(portfolio, 'strategy', None)
    if strategy is None and portfolio.strategy_id:
        from models import Strategy
        strategy = Strategy.query.get(portfolio.strategy_id)
    return (strategy.mode if strategy else None) or 'score'


def _execute_cycle_for_portfolio(portfolio: Portfolio, signals: list, fx_rates: dict) -> list[str]:
    """Führt Kauf-/Verkaufsentscheidungen für ein einzelnes Portfolio aus."""
    from services.strategy_resolver import resolve
    actions = []
    portfolio_params = resolve(portfolio)

    sold_stock_ids: set[int] = set()
    try:
        sl_actions, sold_stock_ids = update_positions(fx_rates, portfolio.id)
        actions.extend(sl_actions)
    except Exception as e:
        log.error(f"Positions-Update Portfolio {portfolio.id}: {e}")

    buy_signals = [s for s in signals if s['action'] == 'BUY']
    for signal in buy_signals:
        if signal['stock_id'] in sold_stock_ids:
            log.info(f"{signal['symbol']}: Kauf übersprungen — im selben Zyklus per SL/TP verkauft")
            continue
        if get_open_positions_count(portfolio.id) >= portfolio_params['max_positions']:
            break
        try:
            ok, msg = execute_buy(signal, fx_rates, portfolio)
            if ok:
                actions.append(msg)
        except Exception as e:
            log.error(f"Kauf {signal['symbol']} Portfolio {portfolio.id}: {e}")

    # Verkaufssignale
    sell_signals = {s['stock_id']: s for s in signals if s['action'] == 'SELL'}
    for pos in Position.query.filter_by(portfolio_id=portfolio.id).all():
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
                log.error(f"Verkauf {pos.stock.symbol} Portfolio {portfolio.id}: {e}")

    return actions


def _update_equity_for_portfolio(portfolio: Portfolio):
    """Berechnet und speichert den Gesamtwert eines einzelnen Portfolios."""
    account = Account.query.filter_by(portfolio_id=portfolio.id).first()
    if not account:
        return

    positions = Position.query.filter_by(portfolio_id=portfolio.id).all()
    positions_value = sum(
        (p.current_price_eur or p.entry_price_eur) * p.shares
        for p in positions
    )
    equity = account.cash_eur + positions_value
    account.equity_eur = equity

    today = date.today()
    history = EquityHistory.query.filter_by(portfolio_id=portfolio.id, date=today).first()

    yesterday = (EquityHistory.query
                 .filter(EquityHistory.portfolio_id == portfolio.id,
                         EquityHistory.date < today)
                 .order_by(EquityHistory.date.desc())
                 .first())

    starting = getattr(portfolio, 'starting_capital', None) or config.STARTING_CAPITAL
    daily_pnl = equity - (yesterday.equity_eur if yesterday else starting)

    if not history:
        history = EquityHistory(date=today, portfolio_id=portfolio.id)
        db.session.add(history)
    history.equity_eur = equity
    history.cash_eur = account.cash_eur
    history.positions_value = positions_value
    history.daily_pnl = daily_pnl

    db.session.commit()


# ─── Haupt-Trading-Schleife ──────────────────────────────────────────────────

def run_trading_cycle(app, portfolio_id: int | None = None) -> list[str]:
    """
    Vollständiger Handelszyklus für alle aktiven Auto-Portfolios
    oder ein einzelnes Portfolio wenn portfolio_id angegeben.

    1. Wechselkurse laden
    2. Preise aktualisieren
    3. Signale berechnen
    4. Pro Portfolio: SL/TP prüfen, kaufen, verkaufen
    5. Equity aller aktiven Portfolios aktualisieren
    """
    from services.data_fetcher import fetch_exchange_rates, update_prices_incremental
    from services.algorithm import generate_signals

    log.info("=== Handelszyklus gestartet ===")
    all_actions = []

    with app.app_context():
        # 1. Wechselkurse aktualisieren
        try:
            fx_rates = fetch_exchange_rates()
        except Exception as e:
            log.error(f"Wechselkurse: {e}")
            fx_rates = {'USD': 1.08, 'GBP': 0.85, 'JPY': 163.0,
                        'CHF': 0.96, 'HKD': 8.45, 'KRW': 1450.0, 'AUD': 1.65, 'EUR': 1.0}

        # 2. Preise inkrementell aktualisieren (geteilt, einmalig)
        try:
            update_prices_incremental(app, config.STOCK_UNIVERSE)
        except Exception as e:
            log.error(f"Preis-Update: {e}")

        # 3. Signale generieren (geteilt, einmalig)
        try:
            signals = generate_signals(app)
        except Exception as e:
            log.error(f"Signal-Generierung: {e}")
            signals = []

        # 4. Portfolios bestimmen — nur Sim-Portfolios (IBKR übernimmt live_runner)
        if portfolio_id is not None:
            portfolios = Portfolio.query.filter(
                Portfolio.id == portfolio_id,
                Portfolio.status == 'active',
                Portfolio.mode == 'auto',
                Portfolio.type == 'sim',
            ).all()
        else:
            portfolios = Portfolio.query.filter(
                Portfolio.status == 'active',
                Portfolio.mode == 'auto',
                Portfolio.type == 'sim',
            ).all()

        for portfolio in portfolios:
            try:
                portfolio_signals = signals
                if _strategy_mode(portfolio) == 'dual_momentum':
                    from services.momentum_signals import generate_momentum_signals
                    portfolio_signals = generate_momentum_signals(portfolio)
                actions = _execute_cycle_for_portfolio(portfolio, portfolio_signals, fx_rates)
                all_actions.extend(actions)
            except Exception as e:
                log.error(f"Zyklus Portfolio {portfolio.id}: {e}")

        # 5. Equity für alle aktiven Portfolios aktualisieren
        try:
            active = Portfolio.query.filter_by(status='active').all()
            for portfolio in active:
                _update_equity_for_portfolio(portfolio)
        except Exception as e:
            log.error(f"Equity-Update: {e}")

    log.info(f"=== Handelszyklus beendet: {len(all_actions)} Aktionen ===")
    return all_actions


def update_equity(app, portfolio_id: int | None = None):
    """Berechnet und speichert den Gesamtwert aller aktiven Portfolios."""
    with app.app_context():
        if portfolio_id is not None:
            portfolios = Portfolio.query.filter_by(id=portfolio_id).all()
        else:
            portfolios = Portfolio.query.filter_by(status='active').all()

        for portfolio in portfolios:
            try:
                _update_equity_for_portfolio(portfolio)
            except Exception as e:
                log.error(f"Equity-Update Portfolio {portfolio.id}: {e}")
