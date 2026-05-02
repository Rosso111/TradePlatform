"""
Live Runner — führt den Handelszyklus mit echten IBKR-Orders aus.
Ersetzt run_trading_cycle() für den Live/Paper-Trading-Modus.
"""
import logging
from datetime import date

from models import db, Account, Position, Trade, Stock, Price, EquityHistory, Portfolio
import config
import config as _config
from services.ibkr_connector import IBKRConnectionPool


def _get_connector(portfolio):
    """Gibt den richtigen IBKR-Connector für ein Portfolio zurück."""
    port = _config.IBKR_LIVE_PORT if portfolio.type == 'ibkr_live' else _config.IBKR_PAPER_PORT
    return IBKRConnectionPool.get(_config.IBKR_HOST, port, _config.IBKR_CLIENT_ID)
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


def execute_live_buy(signal: dict, fx_rates: dict, portfolio: Portfolio) -> tuple[bool, str]:
    """Kauft via IBKR und trägt die Position in die DB ein."""
    account = Account.query.filter_by(portfolio_id=portfolio.id).first()
    if not account:
        return False, f"Kein Konto für Portfolio {portfolio.id}"
    symbol  = signal['symbol']
    stock_id = signal['stock_id']

    # Portfolio-Checks
    if get_open_positions_count(portfolio.id) >= config.MAX_POSITIONS:
        return False, f"{symbol}: Portfolio voll"
    if get_sector_position_count(signal['sector'], portfolio.id) >= config.MAX_POSITIONS_PER_SECTOR:
        return False, f"{symbol}: Sektor voll"
    if already_in_position(stock_id, portfolio.id):
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
    ibkr_account = portfolio.ibkr_account_id or ''
    try:
        conn = _get_connector(portfolio)
        fill_price_usd, fill_qty = conn.place_market_order(symbol, shares, 'BUY', account=ibkr_account)
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
        portfolio_id=portfolio.id,
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
        portfolio_id=portfolio.id,
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

    # Portfolio holen um richtigen Connector + Account zu bestimmen
    from models import Portfolio as _Portfolio
    _portfolio = _Portfolio.query.get(position.portfolio_id)
    ibkr_account = _portfolio.ibkr_account_id if _portfolio else ''
    try:
        conn = _get_connector(_portfolio) if _portfolio else IBKRConnectionPool.get(
            _config.IBKR_HOST, _config.IBKR_PAPER_PORT, _config.IBKR_CLIENT_ID
        )
        fill_price_usd, _ = conn.place_market_order(symbol, qty, 'SELL', account=ibkr_account)
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
        portfolio_id=position.portfolio_id,
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

    account = Account.query.filter_by(portfolio_id=position.portfolio_id).first()
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

def update_live_positions(fx_rates: dict, portfolio_id: int) -> list[str]:
    """Prüft SL/TP für alle offenen Positionen eines Portfolios und sendet ggf. Sell-Orders."""
    actions = []
    for pos in Position.query.filter_by(portfolio_id=portfolio_id).all():
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

def run_live_trading_cycle(app, portfolio_id: int | None = None) -> list[str]:
    """
    Vollständiger Live-Handelszyklus mit IBKR-Ausführung.
    Gleiche Schnittstelle wie run_trading_cycle() in trading_engine.py.
    """
    from services.data_fetcher import fetch_exchange_rates, update_prices_incremental
    from services.algorithm import generate_signals

    log.info("=== IBKR Live-Zyklus gestartet ===")
    all_actions = []

    with app.app_context():
        # 1. Wechselkurse
        try:
            fx_rates = fetch_exchange_rates()
        except Exception as e:
            log.error(f"Wechselkurse: {e}")
            fx_rates = {'USD': 1.08, 'GBP': 0.85, 'JPY': 163.0,
                        'CHF': 0.96, 'HKD': 8.45, 'EUR': 1.0}

        # 2. Preise aktualisieren (einmalig, geteilt)
        try:
            update_prices_incremental(app, config.STOCK_UNIVERSE)
        except Exception as e:
            log.error(f"Preis-Update: {e}")

        # 3. Signale generieren (einmalig, geteilt)
        try:
            signals = generate_signals(app)
        except Exception as e:
            log.error(f"Signal-Generierung: {e}")
            signals = []

        # 4. Portfolios bestimmen — nur IBKR-Portfolios (nicht sim)
        _ibkr_types = ('ibkr_paper', 'ibkr_live')
        if portfolio_id is not None:
            portfolios = Portfolio.query.filter(
                Portfolio.id == portfolio_id,
                Portfolio.status == 'active',
                Portfolio.mode == 'auto',
                Portfolio.type.in_(_ibkr_types),
            ).all()
        else:
            portfolios = Portfolio.query.filter(
                Portfolio.status == 'active',
                Portfolio.mode == 'auto',
                Portfolio.type.in_(_ibkr_types),
            ).all()

        buy_signals = sorted(
            [s for s in signals if s['action'] == 'BUY'],
            key=lambda s: s['score'], reverse=True,
        )
        sell_signals = {s['stock_id']: s for s in signals if s['action'] == 'SELL'}

        for portfolio in portfolios:
            try:
                actions = update_live_positions(fx_rates, portfolio.id)
                all_actions.extend(actions)
            except Exception as e:
                log.error(f"Positions-Update Portfolio {portfolio.id}: {e}")

            for signal in buy_signals:
                if get_open_positions_count(portfolio.id) >= config.MAX_POSITIONS:
                    break
                try:
                    ok, msg = execute_live_buy(signal, fx_rates, portfolio)
                    if ok:
                        all_actions.append(msg)
                except Exception as e:
                    log.error(f"Live-Kauf {signal['symbol']} Portfolio {portfolio.id}: {e}")

            for pos in Position.query.filter_by(portfolio_id=portfolio.id).all():
                if pos.stock_id in sell_signals:
                    sig = sell_signals[pos.stock_id]
                    try:
                        ok, msg = execute_live_sell(
                            pos, fx_rates,
                            reason=f"Verkaufssignal (Score {sig['score']:.0f})"
                        )
                        if ok:
                            all_actions.append(msg)
                    except Exception as e:
                        log.error(f"Live-Verkauf {pos.stock.symbol} Portfolio {portfolio.id}: {e}")

        # 5. Equity
        try:
            update_equity(app)
        except Exception as e:
            log.error(f"Equity-Update: {e}")

    log.info(f"=== IBKR Live-Zyklus beendet: {len(all_actions)} Aktionen ===")
    return all_actions
