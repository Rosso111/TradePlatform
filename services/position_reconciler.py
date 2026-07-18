"""Täglicher Positions-Abgleich DB ↔ IBKR.

Entstanden aus dem Reconciliation-Fund vom 08.07.2026: unentdeckte Shorts
(TNE −17.164 Stk), ein Split-Restbestand (KLAC) und 15 Geister-Positionen.
reconcile_pending_positions() prüft nur offene Pendings — dieses Modul
vergleicht den kompletten Bestand und alarmiert bei jeder Differenz.
"""
import logging

from models import Portfolio, Position
from services.ibkr_connector import clean_symbol

log = logging.getLogger(__name__)

# IBKR führt manche Titel unter anderem Symbol als Yahoo (Corporate Actions).
# Mapping: IBKR-Symbol → clean_symbol(DB-Symbol)
IBKR_SYMBOL_ALIASES = {
    'HONA': 'HON',  # Honeywell nach Aerospace-Abspaltung 2026
}


def compare_positions(db_rows: list[tuple[str, float]],
                      ibkr_rows: list[dict]) -> list[str]:
    """Vergleicht DB-Positionen mit IBKR-Positionen, gibt Differenzen als Textzeilen zurück.

    db_rows:   [(symbol_mit_suffix, shares), ...]
    ibkr_rows: [{'symbol': ..., 'qty': ...}, ...] (Format von IBKRConnector.get_positions)
    """
    ibkr_by_sym: dict[str, float] = {}
    for p in ibkr_rows:
        sym = IBKR_SYMBOL_ALIASES.get(p['symbol'], p['symbol'])
        ibkr_by_sym[sym] = ibkr_by_sym.get(sym, 0) + p['qty']

    db_by_sym: dict[str, float] = {}
    for symbol, shares in db_rows:
        c = clean_symbol(symbol)
        db_by_sym[c] = db_by_sym.get(c, 0) + shares

    diffs = []
    for sym, qty in sorted(ibkr_by_sym.items()):
        if qty < 0:
            diffs.append(f'{sym}: SHORT bei IBKR ({qty:.0f} Stk)!')
        elif sym not in db_by_sym and qty != 0:
            diffs.append(f'{sym}: {qty:.0f} Stk nur bei IBKR (fehlt in DB)')
    for sym, qty in sorted(db_by_sym.items()):
        if sym not in ibkr_by_sym:
            diffs.append(f'{sym}: {qty:.0f} Stk nur in DB (fehlt bei IBKR)')
    for sym in sorted(set(ibkr_by_sym) & set(db_by_sym)):
        if ibkr_by_sym[sym] >= 0 and abs(ibkr_by_sym[sym] - db_by_sym[sym]) > 0.5:
            diffs.append(f'{sym}: IBKR {ibkr_by_sym[sym]:.0f} vs DB {db_by_sym[sym]:.0f} Stk')
    return diffs


def reconcile_all_portfolios(app) -> list[str]:
    """Gleicht alle IBKR-Portfolios ab und alarmiert per Telegram bei Differenzen."""
    import time

    import config
    from services.ibkr_connector import IBKRConnectionPool

    all_diffs = []
    skipped = 0
    with app.app_context():
        portfolios = Portfolio.query.filter(
            Portfolio.status == 'active',
            Portfolio.type.in_(('ibkr_paper', 'ibkr_live')),
        ).all()

        for portfolio in portfolios:
            if not portfolio.ibkr_account_id:
                continue
            port = config.IBKR_LIVE_PORT if portfolio.type == 'ibkr_live' else config.IBKR_PAPER_PORT
            # Das Gateway macht um ~07:00–07:45 seinen täglichen Auto-Restart,
            # daher mehrere Versuche statt sofort aufgeben.
            ibkr_rows = []
            for attempt in range(1, 4):
                # eigene client_id-Range, kollidiert nicht mit Handelszyklus (CLIENT_ID+id)
                conn = IBKRConnectionPool.get(config.IBKR_HOST, port,
                                              config.IBKR_CLIENT_ID + 40 + portfolio.id)
                ibkr_rows = conn.get_positions(portfolio.ibkr_account_id)
                if ibkr_rows:
                    break
                log.warning("Reconciliation %s: keine IBKR-Daten (Versuch %d/3)",
                            portfolio.name, attempt)
                if attempt < 3:
                    time.sleep(90)
            if not ibkr_rows:
                skipped += 1
                continue

            db_rows = [(pos.stock.symbol, pos.shares)
                       for pos in Position.query.filter_by(portfolio_id=portfolio.id).all()]
            diffs = compare_positions(db_rows, ibkr_rows)
            if diffs:
                all_diffs.extend(f'[{portfolio.name}] {d}' for d in diffs)

    if all_diffs:
        log.error("Positions-Reconciliation: %d Differenzen: %s", len(all_diffs), '; '.join(all_diffs))
        try:
            from services.telegram_notifier import send_message, esc
            lines = ['🚨 <b>Positions-Abgleich: Differenzen DB ↔ IBKR</b>']
            lines += [f'• {esc(d)}' for d in all_diffs[:15]]
            if len(all_diffs) > 15:
                lines.append(f'… und {len(all_diffs) - 15} weitere')
            send_message('\n'.join(lines))
        except Exception:
            pass
    elif skipped:
        log.warning("Positions-Reconciliation unvollständig: %d Portfolio(s) ohne IBKR-Daten übersprungen.",
                    skipped)
    else:
        log.info("Positions-Reconciliation: DB und IBKR deckungsgleich.")
    return all_diffs
