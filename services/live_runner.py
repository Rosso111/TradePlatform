"""
Live Runner — führt den Handelszyklus mit echten IBKR-Orders aus.
Ersetzt run_trading_cycle() für den Live/Paper-Trading-Modus.
"""
import logging
from datetime import date

from models import db, Account, Position, Trade, Stock, Price, EquityHistory, Portfolio
import config
import config as _config
from services.ibkr_connector import IBKRConnectionPool, OrderPendingError, clean_symbol


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


def _is_market_bullish(benchmark_symbol: str = 'SPY', period: int = 200) -> bool | None:
    """Prüft ob der Markt bullish ist (Benchmark > SMA{period}). None = kein Urteil möglich."""
    try:
        stock = Stock.query.filter_by(symbol=benchmark_symbol, active=True).first()
        if not stock:
            return None
        prices = (Price.query
                  .filter_by(stock_id=stock.id)
                  .order_by(Price.date.desc())
                  .limit(period)
                  .all())
        if len(prices) < period:
            return None
        closes = [p.close_eur or p.close for p in prices]
        sma = sum(closes) / period
        return closes[0] >= sma
    except Exception as e:
        log.warning("Bear-Market-Check fehlgeschlagen: %s", e)
        return None


# ── Kauf via IBKR ─────────────────────────────────────────────────────────────

def _calc_shares(signal: dict, account: Account, params: dict | None = None) -> int:
    """Berechnet ganzzahlige Stückzahl für IBKR (kein Bruchteil)."""
    p         = params or {}
    equity    = account.equity_eur
    entry_eur = signal['current_price_eur']
    atr       = signal.get('atr')
    mult      = p.get('atr_stop_multiplier',   config.ATR_STOP_MULTIPLIER)
    risk_pct  = p.get('risk_per_trade',        config.RISK_PER_TRADE)
    sl_pct    = p.get('default_stop_loss_pct', config.DEFAULT_STOP_LOSS_PCT)
    max_pct   = p.get('max_position_size',     config.MAX_POSITION_SIZE)
    min_pct   = p.get('min_position_size',     config.MIN_POSITION_SIZE)

    if atr and atr > 0 and entry_eur > 0:
        atr_eur        = (atr / signal['current_price']) * entry_eur
        risk_per_share = mult * atr_eur
        size_by_risk   = (equity * risk_pct / risk_per_share) * entry_eur
    else:
        size_by_risk = equity * risk_pct / sl_pct

    buy_thresh = p.get('buy_threshold', config.SIGNAL_THRESHOLD_BUY)
    score_range = max(1, 100 - buy_thresh)
    score_factor = max(0.0, (signal['score'] - buy_thresh)) / score_range
    size_adjusted = size_by_risk * (0.5 + score_factor * 1.5)  # 0.5x at threshold → 2.0x at score=100

    max_eur      = p.get('max_position_eur', config.MAX_POSITION_EUR)
    position_eur = min(max(size_adjusted, equity * min_pct), min(equity * max_pct, max_eur))
    position_eur = min(position_eur, account.cash_eur * 0.98)

    shares = int(position_eur / entry_eur)
    return max(shares, 0)


def execute_live_buy(signal: dict, fx_rates: dict, portfolio: Portfolio, notify: bool = True) -> tuple[bool, str]:
    """Kauft via IBKR und trägt die Position in die DB ein."""
    from services.strategy_resolver import resolve
    from models import Stock as _Stock

    account  = Account.query.filter_by(portfolio_id=portfolio.id).first()
    if not account:
        return False, f"Kein Konto für Portfolio {portfolio.id}"
    symbol   = signal['symbol']
    stock_id = signal['stock_id']
    stock    = _Stock.query.get(stock_id)
    params   = resolve(portfolio, stock)

    if get_open_positions_count(portfolio.id) >= params['max_positions']:
        return False, f"{symbol}: Portfolio voll"
    if get_sector_position_count(signal['sector'], portfolio.id) >= params['max_positions_per_sector']:
        return False, f"{symbol}: Sektor voll"
    if already_in_position(stock_id, portfolio.id):
        return False, f"{symbol}: Position bereits offen"
    if signal['score'] < params['buy_threshold']:
        return False, f"{symbol}: Score zu niedrig"

    shares = _calc_shares(signal, account, params)
    if shares <= 0:
        return False, f"{symbol}: Stückzahl 0 (zu wenig Kapital)"

    # Vorläufige Kostenprüfung mit aktuellem Schätzpreis
    est_cost = shares * signal['current_price_eur'] * 1.002
    if est_cost > account.cash_eur:
        return False, f"{symbol}: Nicht genug Kapital"

    # ── IBKR Order ────────────────────────────────────────────────────────────
    if not portfolio.ibkr_account_id:
        return False, f"{symbol}: Portfolio {portfolio.id} hat keine IBKR-Account-ID"

    ibkr_account = portfolio.ibkr_account_id
    currency = signal['currency']
    fx_rate  = fx_rates.get(currency, 1.0)
    pending  = False

    try:
        conn = _get_connector(portfolio)
        fill_price_usd, fill_qty = conn.place_market_order(
            symbol, shares, 'BUY', account=ibkr_account,
            currency=signal.get('currency'),
        )
    except OrderPendingError as e:
        # Börse geschlossen — Order liegt bei IBKR, mit Schätzkurs in DB eintragen
        pending        = True
        fill_price_usd = signal['current_price']
        fill_qty       = shares
        log.info(f"{symbol}: Order ausstehend (orderId={e.order_id}) — DB-Eintrag mit Schätzkurs")
    except Exception as e:
        return False, f"{symbol}: IBKR Order fehlgeschlagen — {e}"

    fill_price_eur = fill_price_usd / fx_rate if currency != 'EUR' else fill_price_usd
    position_eur   = fill_qty * fill_price_eur
    commission     = calc_commission(position_eur, params)
    spread         = 0.0 if pending else calc_spread_cost(position_eur, params)
    total_cost     = position_eur + commission + spread

    atr         = signal.get('atr')
    stop_loss   = calc_stop_loss(fill_price_usd, atr, params)
    take_profit = calc_take_profit(fill_price_usd, stop_loss, params)

    # ── DB-Eintrag ────────────────────────────────────────────────────────────
    reason_suffix = ' [IBKR PENDING]' if pending else ' [IBKR LIVE]'
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
        reason=signal.get('reason', '') + reason_suffix,
    )
    db.session.add(pos)

    trade_reason = f"IBKR Pending @ ~{fill_price_usd:.2f}" if pending else f"IBKR Fill @ {fill_price_usd:.2f}"
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
        reason=trade_reason,
    )
    db.session.add(trade)

    account.cash_eur      -= total_cost
    account.total_trades  += 1
    account.total_commission += commission

    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        log.critical(
            f"{symbol}: IBKR-Order ausgeführt, aber DB-Commit fehlgeschlagen — {e}. "
            f"IBKR-Position manuell prüfen!"
        )
        return False, f"{symbol}: DB-Fehler nach IBKR-Order — manuelle Prüfung erforderlich"

    status_label = "PENDING" if pending else "KAUF"
    msg = (f"IBKR {status_label} {symbol}: {fill_qty} Stück @ {fill_price_usd:.2f} "
           f"(= {fill_price_eur:.4f} EUR), Kosten: {total_cost:.2f} EUR")
    log.info(msg)
    if notify:
        try:
            from services.telegram_notifier import notify_trade
            notify_trade('BUY', symbol, fill_qty, fill_price_eur, portfolio_name=portfolio.name)
        except Exception:
            pass
    return True, msg


# ── Verkauf via IBKR ──────────────────────────────────────────────────────────

def execute_live_sell(position: Position, fx_rates: dict, reason: str) -> tuple[bool, str]:
    """Verkauft via IBKR und schließt die Position in der DB."""
    from services.strategy_resolver import resolve
    from models import Portfolio as _Portfolio

    symbol   = position.stock.symbol
    qty      = int(position.shares)
    currency = position.stock.currency
    fx_rate  = fx_rates.get(currency, 1.0)

    if qty <= 0:
        return False, f"{symbol}: Stückzahl 0"

    _portfolio = _Portfolio.query.get(position.portfolio_id)
    params       = resolve(_portfolio, position.stock) if _portfolio else None
    ibkr_account = _portfolio.ibkr_account_id if _portfolio else ''
    try:
        conn = _get_connector(_portfolio) if _portfolio else IBKRConnectionPool.get(
            _config.IBKR_HOST, _config.IBKR_PAPER_PORT, _config.IBKR_CLIENT_ID
        )
        # Short-Sell-Schutz: prüfen ob IBKR die Position wirklich hält
        from services.ibkr_connector import clean_symbol
        ibkr_sym = clean_symbol(symbol)
        ibkr_positions = conn.get_positions(account=ibkr_account)
        ibkr_qty = next((p['qty'] for p in ibkr_positions if p['symbol'] == ibkr_sym), 0)
        if ibkr_qty <= 0:
            return False, f"{symbol}: IBKR hält diese Position nicht — kein Verkauf (Leerverkauf verhindert)"
        sell_qty = min(qty, int(ibkr_qty))
        fill_price_usd, fill_qty = conn.place_market_order(symbol, sell_qty, 'SELL', account=ibkr_account)
    except Exception as e:
        return False, f"{symbol}: IBKR Sell-Order fehlgeschlagen — {e}"

    if fill_qty <= 0:
        return False, f"{symbol}: IBKR Sell-Order — 0 Stück gefüllt"

    partial = fill_qty < qty
    if partial:
        log.warning("%s: Partial Fill SELL — %d/%d Stück ausgeführt, %d Stück verbleiben",
                    symbol, fill_qty, qty, qty - fill_qty)
        try:
            from services.telegram_notifier import send_message
            send_message(
                f"⚠️ <b>Partial Fill SELL {symbol}</b>\n"
                f"{fill_qty}/{qty} Stück ausgeführt — {qty - fill_qty} Stück noch offen"
            )
        except Exception:
            pass

    fill_price_eur = fill_price_usd / fx_rate if currency != 'EUR' else fill_price_usd

    revenue     = fill_qty * fill_price_eur
    commission  = calc_commission(revenue, params)
    spread      = calc_spread_cost(revenue, params)
    net_revenue = revenue - commission - spread

    cost_basis = fill_qty * position.entry_price_eur
    pnl_eur    = net_revenue - cost_basis
    pnl_pct    = (pnl_eur / cost_basis * 100) if cost_basis > 0 else 0

    fill_label = "Partial Fill" if partial else "Fill"
    trade = Trade(
        portfolio_id=position.portfolio_id,
        stock_id=position.stock_id,
        action='SELL',
        shares=float(fill_qty),
        price=fill_price_usd,
        price_eur=fill_price_eur,
        fx_rate=fx_rate,
        commission_eur=commission,
        total_eur=net_revenue,
        pnl_eur=pnl_eur,
        pnl_pct=pnl_pct,
        reason=f"IBKR {fill_label} @ {fill_price_usd:.4f} — {reason}",
    )
    db.session.add(trade)

    account = Account.query.filter_by(portfolio_id=position.portfolio_id).first()
    account.cash_eur      += net_revenue
    account.total_trades  += 1
    account.total_commission += commission
    if pnl_eur > 0:
        account.winning_trades += 1

    if partial:
        remaining = qty - fill_qty
        fill_ratio = remaining / qty
        position.shares         = float(remaining)
        position.cost_eur       = remaining * position.entry_price_eur + (position.commission_eur or 0) * fill_ratio
        position.commission_eur = (position.commission_eur or 0) * fill_ratio
    else:
        db.session.delete(position)

    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        log.critical(
            f"{symbol}: IBKR-Verkauf ausgeführt, aber DB-Commit fehlgeschlagen — {e}. "
            f"Position in DB noch offen, IBKR-Account manuell prüfen!"
        )
        return False, f"{symbol}: DB-Fehler nach IBKR-Verkauf — manuelle Prüfung erforderlich"

    partial_info = f" (Partial {fill_qty}/{qty})" if partial else ""
    msg = (f"IBKR VERKAUF{partial_info} {symbol}: {fill_qty} Stück @ {fill_price_usd:.4f} {currency}, "
           f"P&L: {pnl_eur:+.2f} EUR ({pnl_pct:+.1f}%), Grund: {reason}")
    log.info(msg)
    try:
        from services.telegram_notifier import notify_trade
        port_name = _portfolio.name if _portfolio else ''
        notify_trade('SELL', symbol, fill_qty, fill_price_eur, pnl_eur=pnl_eur, portfolio_name=port_name)
    except Exception:
        pass
    return True, msg


# ── Fill-Reconciliation ───────────────────────────────────────────────────────

def reconcile_pending_positions(portfolio: Portfolio, fx_rates: dict) -> list[str]:
    """Gleicht PENDING-Positionen (Schätzpreis) mit echten IBKR-Fill-Preisen ab.
    Wird nach dem Account-Sync aufgerufen. Gibt eine Liste von Aktionsmeldungen zurück."""
    actions = []
    pending_positions = Position.query.filter(
        Position.portfolio_id == portfolio.id,
        Position.reason.like('%[IBKR PENDING]%'),
    ).all()
    if not pending_positions:
        return actions

    try:
        conn = _get_connector(portfolio)
        # IBKR gibt saubere Symbole ohne Suffix zurück (z.B. 'BAS' statt 'BAS.DE')
        ibkr_positions = {p['symbol']: p for p in conn.get_positions(portfolio.ibkr_account_id or '')}
    except Exception as e:
        log.warning(f"Reconciliation: IBKR-Positionen nicht abrufbar — {e}")
        return actions

    for pos in pending_positions:
        symbol = pos.stock.symbol
        ibkr_pos = ibkr_positions.get(clean_symbol(symbol))
        if not ibkr_pos:
            log.info(f"Reconciliation {symbol}: noch keine IBKR-Position — Order evtl. noch offen")
            continue

        avg_cost = ibkr_pos.get('avg_cost', 0)
        if not avg_cost or avg_cost <= 0:
            continue

        # avg_cost bei EUR-Aktien ist in EUR, bei USD in USD
        currency = pos.stock.currency
        fx_rate  = fx_rates.get(currency, pos.entry_rate or 1.0)
        avg_cost_eur = avg_cost / fx_rate if currency != 'EUR' else avg_cost

        diff_pct = abs(avg_cost_eur - pos.entry_price_eur) / pos.entry_price_eur if pos.entry_price_eur else 1.0
        if diff_pct < 0.001:
            # Preisdifferenz < 0.1% — Schätzkurs war nah genug, nur Marker entfernen
            pos.reason = pos.reason.replace('[IBKR PENDING]', '[IBKR LIVE]')
            db.session.commit()
            log.info(f"Reconciliation {symbol}: Fill-Preis stimmt überein ({avg_cost_eur:.4f} EUR)")
            continue

        old_price_eur = pos.entry_price_eur
        shares        = pos.shares

        pos.entry_price     = avg_cost
        pos.entry_price_eur = avg_cost_eur
        pos.current_price   = avg_cost
        pos.current_price_eur = avg_cost_eur
        pos.cost_eur        = shares * avg_cost_eur + (pos.commission_eur or 0)
        pos.reason          = pos.reason.replace('[IBKR PENDING]', '[IBKR LIVE]')

        # Auch den dazugehörigen Trade-Eintrag korrigieren
        matching_trade = (Trade.query
                          .filter_by(portfolio_id=portfolio.id, stock_id=pos.stock_id, action='BUY')
                          .order_by(Trade.executed_at.desc())
                          .first())
        if matching_trade and 'IBKR Pending' in (matching_trade.reason or ''):
            matching_trade.price     = avg_cost
            matching_trade.price_eur = avg_cost_eur
            matching_trade.total_eur = shares * avg_cost_eur + (matching_trade.commission_eur or 0)
            matching_trade.reason    = matching_trade.reason.replace('IBKR Pending', 'IBKR Fill')

        db.session.commit()

        msg = (f"Reconciliation {symbol}: Schätzkurs {old_price_eur:.4f} EUR → "
               f"echter Fill-Preis {avg_cost_eur:.4f} EUR (Δ {diff_pct*100:+.2f}%)")
        log.info(msg)
        actions.append(msg)

    return actions


# ── Positionen überwachen (SL/TP via IBKR) ───────────────────────────────────

def update_live_positions(fx_rates: dict, portfolio_id: int) -> tuple[list[str], set[int]]:
    """Prüft SL/TP für alle offenen Positionen eines Portfolios und sendet ggf. Sell-Orders.
    Gibt (actions, sold_stock_ids) zurück."""
    from services.strategy_resolver import resolve
    actions        = []
    sold_stock_ids: set[int] = set()
    portfolio      = Portfolio.query.get(portfolio_id)

    for pos in Position.query.filter_by(portfolio_id=portfolio_id).all():
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
        # LSE prices (GBP currency) are stored in GBX (pence) — normalize to GBP
        if currency == 'GBP':
            current_price /= 100.0
        current_price_eur = latest.close_eur or (current_price / fx_rate)

        pos.current_price     = current_price
        pos.current_price_eur = current_price_eur

        trailing_pct = params.get('trailing_stop_pct', config.TRAILING_STOP_PCT)
        if current_price > (pos.highest_price or pos.entry_price):
            pos.highest_price = current_price
            new_trailing = current_price * (1 - trailing_pct)
            if new_trailing > (pos.trailing_stop or 0):
                pos.trailing_stop = new_trailing

        effective_stop = max(pos.stop_loss or 0, pos.trailing_stop or 0)

        if effective_stop > 0 and current_price <= effective_stop:
            ok, msg = execute_live_sell(pos, fx_rates, reason='Stop-Loss ausgelöst')
            if ok:
                sold_stock_ids.add(pos.stock_id)
            actions.append(msg)
        elif pos.take_profit and current_price >= pos.take_profit:
            ok, msg = execute_live_sell(pos, fx_rates, reason='Take-Profit erreicht')
            if ok:
                sold_stock_ids.add(pos.stock_id)
            actions.append(msg)

    db.session.commit()
    return actions, sold_stock_ids


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

        for portfolio in portfolios:
            from services.strategy_resolver import resolve as _resolve
            port_params = _resolve(portfolio)

            # Bear-Market-Filter: strengere Schwellen wenn SPY < SMA200
            benchmark = port_params.get('regime_filter_symbol', 'SPY')
            regime_period = int(port_params.get('regime_filter_period', 200))
            market_bullish = _is_market_bullish(benchmark, regime_period)
            if market_bullish is False:
                bear_buy_thresh = port_params.get('bear_market_buy_threshold', 65)
                bear_max_pos = port_params.get('bear_market_max_positions', 20)
                log.warning("Bear-Market erkannt (%s < SMA%d): buy_threshold=%s, max_positions=%s",
                            benchmark, regime_period, bear_buy_thresh, bear_max_pos)
                port_params = dict(port_params)
                port_params['buy_threshold'] = bear_buy_thresh
                port_params['max_positions'] = bear_max_pos

            portfolio_signals = generate_signals(app, portfolio=portfolio)
            buy_signals = sorted(
                [s for s in portfolio_signals if s['score'] >= port_params['buy_threshold']],
                key=lambda s: s['score'], reverse=True,
            )
            sell_signals = {
                s['stock_id']: s
                for s in portfolio_signals if s['score'] <= port_params['sell_threshold']
            }

            # Kontostand von IBKR holen und in DB synchronisieren
            try:
                conn = _get_connector(portfolio)
                ibkr_data = conn.get_account_values(portfolio.ibkr_account_id or '')
                account = Account.query.filter_by(portfolio_id=portfolio.id).first()
                if account and ibkr_data:
                    account.cash_eur   = ibkr_data['cash']
                    account.equity_eur = ibkr_data['equity']
                    db.session.commit()
                    log.info(f"Portfolio {portfolio.id}: IBKR-Cash={ibkr_data['cash']:.2f}€, "
                             f"Equity={ibkr_data['equity']:.2f}€")
            except Exception as e:
                log.warning(f"IBKR-Account-Sync Portfolio {portfolio.id} fehlgeschlagen: {e}")

            try:
                recon_actions = reconcile_pending_positions(portfolio, fx_rates)
                all_actions.extend(recon_actions)
            except Exception as e:
                log.warning(f"Fill-Reconciliation Portfolio {portfolio.id}: {e}")

            sold_stock_ids: set[int] = set()
            try:
                pos_actions, sold_stock_ids = update_live_positions(fx_rates, portfolio.id)
                all_actions.extend(pos_actions)
            except Exception as e:
                log.error(f"Positions-Update Portfolio {portfolio.id}: {e}")

            batch_buys = []  # (symbol, qty, price_eur, total_eur) für Sammel-Nachricht
            for signal in buy_signals:
                if signal['stock_id'] in sold_stock_ids:
                    log.info(f"{signal['symbol']}: Kauf übersprungen — im selben Zyklus per SL/TP verkauft")
                    continue
                if get_open_positions_count(portfolio.id) >= port_params['max_positions']:
                    break
                try:
                    ok, msg = execute_live_buy(signal, fx_rates, portfolio, notify=False)
                    if ok:
                        all_actions.append(msg)
                        # Daten für Sammel-Nachricht extrahieren
                        parts = msg.split()
                        try:
                            sym = signal['symbol']
                            qty_idx = parts.index('Stück') - 1
                            qty = int(parts[qty_idx])
                            price_eur = signal['current_price_eur']
                            total_eur = qty * price_eur
                            batch_buys.append((sym, qty, price_eur, total_eur))
                        except Exception:
                            batch_buys.append((signal['symbol'], 0, 0, 0))
                except Exception as e:
                    log.error(f"Live-Kauf {signal['symbol']} Portfolio {portfolio.id}: {e}")

            if batch_buys:
                try:
                    from services.telegram_notifier import notify_buy_batch
                    from models import Account as _Acc
                    acc = _Acc.query.filter_by(portfolio_id=portfolio.id).first()
                    cash = acc.cash_eur if acc else 0
                    notify_buy_batch(batch_buys, cash, portfolio_name=portfolio.name)
                except Exception as e:
                    log.warning(f"Sammel-Benachrichtigung fehlgeschlagen: {e}")

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
