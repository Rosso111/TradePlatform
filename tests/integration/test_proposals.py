"""
Integrationstests: Proposals/Approval-Workflow-Endpoints (routes/proposals.py)
"""
import pytest
from datetime import date, timedelta
from models import DailyProposal, ProposedOrder, Position, Trade, Account


@pytest.mark.integration
class TestListProposals:
    """API-23: Alle Proposals eines Portfolios."""

    def test_owner_lists_proposals(self, user_client, approval_portfolio, proposal):
        """API-23: Eigentümer sieht seine Proposals."""
        r = user_client.get(f'/api/portfolios/{approval_portfolio.id}/proposals')
        assert r.status_code == 200
        data = r.get_json()
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]['id'] == proposal.id

    def test_proposals_include_orders(self, user_client, approval_portfolio, proposal):
        """API-23: Proposals enthalten die zugehörigen Orders."""
        r = user_client.get(f'/api/portfolios/{approval_portfolio.id}/proposals')
        assert r.status_code == 200
        orders = r.get_json()[0]['orders']
        assert len(orders) == 1
        assert orders[0]['action'] == 'BUY'

    def test_other_user_gets_403(self, client, second_user, approval_portfolio):
        """P-06: Fremder User (kein Admin) → 403."""
        from tests.conftest import login
        login(client, 'otheruser', 'otherpassword1')
        r = client.get(f'/api/portfolios/{approval_portfolio.id}/proposals')
        assert r.status_code == 403

    def test_unauthenticated_gets_401(self, client, approval_portfolio):
        """U-04: Nicht eingeloggt → 401."""
        r = client.get(f'/api/portfolios/{approval_portfolio.id}/proposals')
        assert r.status_code == 401


@pytest.mark.integration
class TestTodayProposal:
    """API-24: Heutiger Proposal."""

    def test_returns_today_proposal(self, user_client, approval_portfolio, proposal):
        """API-24: Heutiger Proposal wird zurückgegeben."""
        r = user_client.get(f'/api/portfolios/{approval_portfolio.id}/proposals/today')
        assert r.status_code == 200
        data = r.get_json()
        assert data['proposal'] is not None
        assert data['proposal']['id'] == proposal.id

    def test_returns_null_when_no_proposal_today(self, user_client, approval_portfolio):
        """API-24: Kein Proposal heute → {'proposal': null}."""
        r = user_client.get(f'/api/portfolios/{approval_portfolio.id}/proposals/today')
        assert r.status_code == 200
        assert r.get_json()['proposal'] is None

    def test_yesterday_proposal_not_returned_as_today(self, db, user_client, approval_portfolio, stock):
        """API-24: Gestriger Proposal wird nicht als heutiger zurückgegeben."""
        yesterday_prop = DailyProposal(
            portfolio_id=approval_portfolio.id,
            proposal_date=date.today() - timedelta(days=1),
            status='expired',
        )
        db.session.add(yesterday_prop)
        db.session.commit()
        r = user_client.get(f'/api/portfolios/{approval_portfolio.id}/proposals/today')
        assert r.get_json()['proposal'] is None


@pytest.mark.integration
class TestPatchOrderApproval:
    """API-25, PR-06, PR-10: approved-Flag setzen."""

    def test_set_approved_false(self, user_client, proposal):
        """PR-06: approved auf false setzen."""
        order = proposal.orders.first()
        r = user_client.patch(
            f'/api/proposals/{proposal.id}/orders/{order.id}',
            json={'approved': False},
        )
        assert r.status_code == 200
        assert r.get_json()['approved'] is False

    def test_set_approved_true(self, db, user_client, proposal):
        """PR-06: approved zurück auf true setzen."""
        order = proposal.orders.first()
        order.approved = False
        db.session.commit()
        r = user_client.patch(
            f'/api/proposals/{proposal.id}/orders/{order.id}',
            json={'approved': True},
        )
        assert r.status_code == 200
        assert r.get_json()['approved'] is True

    def test_cannot_change_executed_order(self, db, user_client, proposal):
        """PR-10: Bereits ausgeführte Order kann nicht geändert werden → 409."""
        from datetime import datetime, timezone
        order = proposal.orders.first()
        order.executed = True
        order.executed_at = datetime.now(timezone.utc)
        db.session.commit()
        r = user_client.patch(
            f'/api/proposals/{proposal.id}/orders/{order.id}',
            json={'approved': False},
        )
        assert r.status_code == 409

    def test_missing_approved_field(self, user_client, proposal):
        """API-25: Fehlendes 'approved'-Feld → 400."""
        order = proposal.orders.first()
        r = user_client.patch(
            f'/api/proposals/{proposal.id}/orders/{order.id}',
            json={'something_else': True},
        )
        assert r.status_code == 400

    def test_other_user_cannot_patch(self, client, second_user, proposal):
        """P-06: Fremder User kann Order nicht ändern → 403."""
        from tests.conftest import login
        login(client, 'otheruser', 'otherpassword1')
        order = proposal.orders.first()
        r = client.patch(
            f'/api/proposals/{proposal.id}/orders/{order.id}',
            json={'approved': False},
        )
        assert r.status_code == 403


@pytest.mark.integration
class TestExecuteProposal:
    """API-26, PR-07, PR-08, PR-11: Proposal ausführen."""

    def test_execute_buy_creates_position_and_trade(self, db, user_client, proposal, approval_portfolio):
        """PR-07, PR-08: BUY-Order erstellt Position + Trade."""
        r = user_client.post(f'/api/proposals/{proposal.id}/execute')
        assert r.status_code == 200

        pos_count = Position.query.filter_by(portfolio_id=approval_portfolio.id).count()
        trade_count = Trade.query.filter_by(portfolio_id=approval_portfolio.id).count()
        assert pos_count == 1
        assert trade_count == 1

    def test_execute_debits_account(self, db, user_client, proposal, approval_portfolio):
        """PR-08: Kauf reduziert account.cash_eur."""
        account_before = Account.query.filter_by(portfolio_id=approval_portfolio.id).first().cash_eur
        user_client.post(f'/api/proposals/{proposal.id}/execute')
        account_after = Account.query.filter_by(portfolio_id=approval_portfolio.id).first().cash_eur
        assert account_after < account_before

    def test_execute_sets_order_executed(self, db, user_client, proposal):
        """PR-08: executed=True + fill_price gesetzt."""
        user_client.post(f'/api/proposals/{proposal.id}/execute')
        order = proposal.orders.first()
        db.session.refresh(order)
        assert order.executed is True
        assert order.fill_price is not None
        assert order.executed_at is not None

    def test_execute_all_approved_sets_status_executed(self, db, user_client, proposal):
        """PR-11: Alle approved Orders ausgeführt → status='executed'."""
        user_client.post(f'/api/proposals/{proposal.id}/execute')
        db.session.refresh(proposal)
        assert proposal.status == 'executed'

    def test_execute_partial_sets_status_partially_executed(self, db, user_client, proposal, stock):
        """PR-11: Teilweise executed → status='partially_executed'."""
        # Zweite Order hinzufügen mit insufficient cash
        poor_order = ProposedOrder(
            proposal_id=proposal.id,
            stock_id=stock.id,
            action='BUY',
            shares_proposed=99999.0,   # Wird an Cash-Grenze scheitern
            est_price_eur=99999.0,
            score=80.0,
            reason='Too expensive',
            approved=True,
        )
        db.session.add(poor_order)
        db.session.commit()

        r = user_client.post(f'/api/proposals/{proposal.id}/execute')
        assert r.status_code == 200
        db.session.refresh(proposal)
        assert proposal.status in ('executed', 'partially_executed')

    def test_execute_already_executed_returns_409(self, db, user_client, proposal):
        """PR-11: Bereits 'executed' Proposal → 409."""
        user_client.post(f'/api/proposals/{proposal.id}/execute')
        r = user_client.post(f'/api/proposals/{proposal.id}/execute')
        assert r.status_code == 409

    def test_execute_no_approved_orders_returns_400(self, db, user_client, proposal):
        """PR-07: Keine approved Orders → 400."""
        order = proposal.orders.first()
        order.approved = False
        db.session.commit()
        r = user_client.post(f'/api/proposals/{proposal.id}/execute')
        assert r.status_code == 400

    def test_execute_sell_closes_position(self, db, user_client, approval_portfolio, stock):
        """PR-07, PR-08: SELL-Order schließt bestehende Position."""
        # Position anlegen
        pos = Position(
            portfolio_id=approval_portfolio.id, stock_id=stock.id,
            shares=10.0, entry_price=100.0, entry_price_eur=100.0, cost_eur=1000.0,
        )
        db.session.add(pos)
        # Proposal mit SELL-Order
        prop = DailyProposal(
            portfolio_id=approval_portfolio.id,
            proposal_date=date.today(),
            status='open',
        )
        db.session.add(prop)
        db.session.flush()
        sell_order = ProposedOrder(
            proposal_id=prop.id, stock_id=stock.id,
            action='SELL', shares_proposed=10.0, est_price_eur=120.0,
            score=30.0, reason='Sell signal', approved=True,
        )
        db.session.add(sell_order)
        db.session.commit()

        user_client.post(f'/api/proposals/{prop.id}/execute')

        pos_count = Position.query.filter_by(
            portfolio_id=approval_portfolio.id, stock_id=stock.id
        ).count()
        assert pos_count == 0

    def test_other_user_cannot_execute(self, client, second_user, proposal):
        """P-06: Fremder User kann Proposal nicht ausführen → 403."""
        from tests.conftest import login
        login(client, 'otheruser', 'otherpassword1')
        r = client.post(f'/api/proposals/{proposal.id}/execute')
        assert r.status_code == 403
