"""
Proposals/Approval-Workflow Routes
Tagesvorschläge lesen, genehmigen und ausführen.
"""
import logging
from datetime import date, datetime, timezone

import config
from flask import Blueprint, jsonify, request
from flask_login import login_required, current_user
from app import limiter

from models import db, Portfolio, DailyProposal, ProposedOrder, Position, Trade, Account

log = logging.getLogger(__name__)

proposals_bp = Blueprint('proposals', __name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_portfolio_or_403(portfolio_id):
    """Portfolio-Ownership-Guard — analog zu portfolios.py."""
    portfolio = Portfolio.query.get_or_404(portfolio_id)
    if portfolio.user_id != current_user.id and current_user.role != 'admin':
        return None, jsonify({'error': 'Keine Berechtigung'}), 403
    return portfolio, None, None


def _get_proposal_or_403(proposal_id):
    """Proposal existiert + gehört einem Portfolio des aktuellen Users."""
    proposal = DailyProposal.query.get_or_404(proposal_id)
    portfolio = Portfolio.query.get(proposal.portfolio_id)
    if portfolio is None:
        return None, jsonify({'error': 'Portfolio nicht gefunden'}), 404
    if portfolio.user_id != current_user.id and current_user.role != 'admin':
        return None, jsonify({'error': 'Keine Berechtigung'}), 403
    return proposal, None, None


def _proposal_with_orders(proposal: DailyProposal) -> dict:
    """to_dict inkl. aller Orders."""
    data = proposal.to_dict()
    data['orders'] = [o.to_dict() for o in proposal.orders.all()]
    return data


def _calc_commission(value_eur: float) -> float:
    commission = value_eur * config.COMMISSION_RATE
    return max(commission, config.MIN_COMMISSION)


def _calc_spread_cost(value_eur: float) -> float:
    return value_eur * config.SPREAD_RATE


# ---------------------------------------------------------------------------
# API-23: Alle Proposals eines Portfolios
# ---------------------------------------------------------------------------

@proposals_bp.route('/api/portfolios/<int:portfolio_id>/proposals', methods=['GET'])
@login_required
def list_proposals(portfolio_id):
    """Implements: API-23"""
    portfolio, err, code = _get_portfolio_or_403(portfolio_id)
    if err:
        return err, code

    proposals = (DailyProposal.query
                 .filter_by(portfolio_id=portfolio_id)
                 .order_by(DailyProposal.proposal_date.desc())
                 .all())
    return jsonify([_proposal_with_orders(p) for p in proposals])


# ---------------------------------------------------------------------------
# API-24: Heutiger Proposal
# ---------------------------------------------------------------------------

@proposals_bp.route('/api/portfolios/<int:portfolio_id>/proposals/today', methods=['GET'])
@login_required
def get_today_proposal(portfolio_id):
    """Implements: API-24"""
    portfolio, err, code = _get_portfolio_or_403(portfolio_id)
    if err:
        return err, code

    today = date.today()
    proposal = DailyProposal.query.filter_by(
        portfolio_id=portfolio_id,
        proposal_date=today,
    ).first()

    if not proposal:
        return jsonify({'proposal': None})

    return jsonify({'proposal': _proposal_with_orders(proposal)})


# ---------------------------------------------------------------------------
# API-25: approved-Flag einer Order setzen
# ---------------------------------------------------------------------------

@proposals_bp.route('/api/proposals/<int:proposal_id>/orders/<int:order_id>', methods=['PATCH'])
@login_required
def patch_order_approval(proposal_id, order_id):
    """Implements: API-25, PR-06, PR-10"""
    proposal, err, code = _get_proposal_or_403(proposal_id)
    if err:
        return err, code

    order = ProposedOrder.query.get_or_404(order_id)
    if order.proposal_id != proposal_id:
        return jsonify({'error': 'Order gehört nicht zu diesem Proposal'}), 400

    # Implements: PR-10 — bereits ausgeführte Orders können nicht geändert werden
    if order.executed:
        return jsonify({'error': 'Bereits ausgeführte Orders können nicht geändert werden'}), 409

    data = request.get_json(silent=True) or {}
    if 'approved' not in data:
        return jsonify({'error': 'Feld "approved" erforderlich'}), 400

    order.approved = bool(data['approved'])
    db.session.commit()
    return jsonify(order.to_dict())


# ---------------------------------------------------------------------------
# API-26: Alle approved Orders ausführen
# ---------------------------------------------------------------------------

@proposals_bp.route('/api/proposals/<int:proposal_id>/execute', methods=['POST'])
@login_required
@limiter.limit("5 per minute")
def execute_proposal(proposal_id):
    """Implements: API-26, PR-07, PR-08, PR-11"""
    proposal, err, code = _get_proposal_or_403(proposal_id)
    if err:
        return err, code

    if proposal.status in ('executed', 'expired'):
        return jsonify({'error': f'Proposal ist bereits im Status "{proposal.status}"'}), 409

    portfolio_id = proposal.portfolio_id
    account = Account.query.filter_by(portfolio_id=portfolio_id).first()
    if not account:
        return jsonify({'error': 'Kein Account für dieses Portfolio'}), 500

    pending = [o for o in proposal.orders.all() if o.approved and not o.executed]
    if not pending:
        return jsonify({'error': 'Keine ausstehenden approved Orders'}), 400

    results = []
    now = datetime.now(timezone.utc)

    for order in pending:
        fill_price = order.est_price_eur
        total_value = order.shares_proposed * fill_price

        if order.action == 'BUY':
            commission = _calc_commission(total_value)
            spread = _calc_spread_cost(total_value)
            total_cost = total_value + commission + spread

            if total_cost > account.cash_eur:
                results.append({
                    'order_id': order.id,
                    'symbol': order.stock.symbol,
                    'action': 'BUY',
                    'success': False,
                    'reason': f'Nicht genug Kapital ({account.cash_eur:.2f} EUR < {total_cost:.2f} EUR)',
                })
                continue

            entry_price_eur = fill_price * (1 + config.SPREAD_RATE)
            shares = total_value / entry_price_eur

            pos = Position(
                portfolio_id=portfolio_id,
                stock_id=order.stock_id,
                shares=shares,
                entry_price=entry_price_eur,
                entry_price_eur=entry_price_eur,
                entry_rate=1.0,
                current_price=fill_price,
                current_price_eur=fill_price,
                cost_eur=total_cost,
                commission_eur=commission,
                reason=order.reason or 'Proposal-Ausführung',
            )
            db.session.add(pos)

            trade = Trade(
                portfolio_id=portfolio_id,
                stock_id=order.stock_id,
                action='BUY',
                shares=shares,
                price=entry_price_eur,
                price_eur=entry_price_eur,
                fx_rate=1.0,
                commission_eur=commission,
                total_eur=total_cost,
                pnl_eur=0.0,
                reason=order.reason or 'Proposal-Ausführung',
            )
            db.session.add(trade)

            account.cash_eur -= total_cost
            account.total_trades += 1
            account.total_commission += commission

        elif order.action == 'SELL':
            position = (Position.query
                        .filter_by(portfolio_id=portfolio_id, stock_id=order.stock_id)
                        .first())
            if not position:
                results.append({
                    'order_id': order.id,
                    'symbol': order.stock.symbol,
                    'action': 'SELL',
                    'success': False,
                    'reason': 'Keine offene Position gefunden',
                })
                continue

            sell_shares = min(order.shares_proposed, position.shares)
            revenue = sell_shares * fill_price
            commission = _calc_commission(revenue)
            spread = _calc_spread_cost(revenue)
            net_revenue = revenue - commission - spread

            cost_basis = sell_shares * position.entry_price_eur
            pnl_eur = net_revenue - cost_basis
            pnl_pct = (pnl_eur / cost_basis * 100) if cost_basis > 0 else 0

            trade = Trade(
                portfolio_id=portfolio_id,
                stock_id=order.stock_id,
                action='SELL',
                shares=sell_shares,
                price=fill_price,
                price_eur=fill_price,
                fx_rate=1.0,
                commission_eur=commission,
                total_eur=net_revenue,
                pnl_eur=pnl_eur,
                pnl_pct=pnl_pct,
                reason=order.reason or 'Proposal-Ausführung',
            )
            db.session.add(trade)

            if sell_shares >= position.shares:
                db.session.delete(position)
            else:
                position.shares -= sell_shares
                position.cost_eur -= (sell_shares / (position.shares + sell_shares)) * position.cost_eur

            account.cash_eur += net_revenue
            account.total_trades += 1
            account.total_commission += commission
            if pnl_eur > 0:
                account.winning_trades += 1

        else:
            results.append({
                'order_id': order.id,
                'symbol': order.stock.symbol,
                'action': order.action,
                'success': False,
                'reason': f'Unbekannte Action: {order.action}',
            })
            continue

        # Implements: PR-08 — executed + fill_price setzen
        order.executed = True
        order.fill_price = fill_price
        order.executed_at = now
        results.append({
            'order_id': order.id,
            'symbol': order.stock.symbol,
            'action': order.action,
            'success': True,
            'fill_price': round(fill_price, 4),
        })

    # Implements: PR-11 — Proposal-Status aktualisieren
    all_orders = proposal.orders.all()
    executed_count = sum(1 for o in all_orders if o.executed)
    approved_count = sum(1 for o in all_orders if o.approved)

    if executed_count == 0:
        pass  # Status bleibt 'open' wenn alle Orders fehlgeschlagen
    elif executed_count >= approved_count:
        proposal.status = 'executed'
        proposal.executed_at = now
    else:
        proposal.status = 'partially_executed'

    db.session.commit()
    return jsonify({
        'proposal': _proposal_with_orders(proposal),
        'results': results,
    })
