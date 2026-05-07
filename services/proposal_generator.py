"""
Proposal Generator
Tägliche Vorschläge für Approval-Portfolios erzeugen und veraltete abläuft lassen.
"""
import logging
from datetime import date, timedelta, datetime, timezone

import config

log = logging.getLogger(__name__)


def generate_daily_proposals(app) -> int:
    """Implements: PR-01, PR-02, PR-03, PR-04, PR-05

    Erzeugt für jedes aktive Approval-Portfolio einen DailyProposal
    auf Basis der gestrigen Schlusskurse. Gibt Anzahl erzeugter Proposals zurück.
    """
    from services.algorithm import generate_signals_for_date
    from services.trading_engine import calc_position_size

    yesterday = date.today() - timedelta(days=1)

    # generate_signals_for_date managt eigenen App-Context → zuerst aufrufen
    try:
        signals = generate_signals_for_date(app, yesterday)
    except Exception as e:
        log.error("Proposal-Generator: Signals konnten nicht berechnet werden: %s", e)
        return 0

    buy_signals = [s for s in signals if s['action'] == 'BUY'
                   and s['score'] >= config.SIGNAL_THRESHOLD_BUY]
    sell_signals = {s['stock_id']: s for s in signals if s['action'] == 'SELL'}

    created = 0
    today = date.today()

    with app.app_context():
        from models import db, Portfolio, Account, Position, DailyProposal, ProposedOrder

        # Implements: PR-01 — nur ein Proposal pro Portfolio und Tag
        portfolios = Portfolio.query.filter_by(mode='approval', status='active').all()

        for portfolio in portfolios:
            if DailyProposal.query.filter_by(
                portfolio_id=portfolio.id,
                proposal_date=today,
            ).first():
                log.debug("Proposal für Portfolio %d existiert bereits.", portfolio.id)
                continue

            account = Account.query.filter_by(portfolio_id=portfolio.id).first()
            if not account:
                log.warning("Portfolio %d hat kein Account — Proposal übersprungen.", portfolio.id)
                continue

            open_positions = Position.query.filter_by(portfolio_id=portfolio.id).all()
            open_stock_ids = {p.stock_id for p in open_positions}
            free_slots = config.MAX_POSITIONS - len(open_positions)

            proposal_orders = []

            # BUY-Vorschläge: Score >= Schwelle, Position noch nicht offen
            for sig in buy_signals:
                if free_slots <= 0:
                    break
                if sig['stock_id'] in open_stock_ids:
                    continue

                position_eur = calc_position_size(account, sig)
                if position_eur < 50:
                    continue

                est_price = sig['current_price_eur']
                if not est_price or est_price <= 0:
                    continue

                # Implements: PR-02 — Schätzpreis = gestriger Schlusskurs
                shares = round(position_eur / est_price, 4)
                proposal_orders.append(ProposedOrder(
                    stock_id=sig['stock_id'],
                    action='BUY',
                    shares_proposed=shares,
                    est_price_eur=round(est_price, 4),
                    score=round(sig['score'], 1),
                    reason=(sig.get('reason') or '')[:500],
                    approved=True,  # Implements: PR-05
                ))
                free_slots -= 1

            # SELL-Vorschläge: offene Positionen mit Sell-Signal
            for pos in open_positions:
                if pos.stock_id not in sell_signals:
                    continue
                sig = sell_signals[pos.stock_id]
                est_price = sig['current_price_eur']
                proposal_orders.append(ProposedOrder(
                    stock_id=pos.stock_id,
                    action='SELL',
                    shares_proposed=round(pos.shares, 4),
                    est_price_eur=round(est_price, 4) if est_price else 0.0,
                    score=round(sig['score'], 1),
                    reason=(sig.get('reason') or '')[:500],
                    approved=True,  # Implements: PR-05
                ))

            if not proposal_orders:
                log.info("Portfolio %d: keine Signale → kein Proposal.", portfolio.id)
                continue

            # Implements: PR-04 — Proposal mit Orders anlegen
            proposal = DailyProposal(
                portfolio_id=portfolio.id,
                proposal_date=today,
                status='open',
            )
            db.session.add(proposal)
            db.session.flush()

            for order in proposal_orders:
                order.proposal_id = proposal.id
                db.session.add(order)

            db.session.commit()
            created += 1
            log.info(
                "Proposal für Portfolio %d erstellt: %d Orders (%d BUY, %d SELL).",
                portfolio.id,
                len(proposal_orders),
                sum(1 for o in proposal_orders if o.action == 'BUY'),
                sum(1 for o in proposal_orders if o.action == 'SELL'),
            )

    return created


def expire_stale_proposals(app) -> int:
    """Implements: PR-09

    Setzt alle offenen Proposals von vor heute auf 'expired'.
    Gibt Anzahl abgelaufener Proposals zurück.
    """
    with app.app_context():
        from models import db, DailyProposal

        today = date.today()
        stale = DailyProposal.query.filter(
            DailyProposal.proposal_date < today,
            DailyProposal.status.in_(['open', 'partially_executed']),
        ).all()

        for proposal in stale:
            proposal.status = 'expired'

        if stale:
            db.session.commit()
            log.info("Proposals abgelaufen: %d Stück.", len(stale))

        return len(stale)
