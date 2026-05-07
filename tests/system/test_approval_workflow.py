"""
Systemtest: Vollständiger Approval-Workflow (End-to-End)
Deckt PR-01 bis PR-11, U-01, U-06, P-01, P-13 ab.
"""
import pytest
from datetime import date
from tests.conftest import login


@pytest.mark.system
class TestApprovalWorkflowEndToEnd:
    """
    Vollständiger User-Flow:
    Admin legt User an → User loggt ein → legt Approval-Portfolio an →
    Proposal erscheint → User modifiziert Approval → führt aus → Status korrekt.
    """

    def test_full_approval_flow(self, db, client, admin_user, stock):
        """
        PR-01..PR-11, U-01, U-06, P-01, P-13:
        Vollständiger Approval-Workflow von Useranlegung bis Ausführung.
        """
        # Schritt 1: Admin legt User an (U-06)
        login(client, 'admin', 'adminpassword1')
        r = client.post('/api/users', json={
            'username': 'flowuser', 'password': 'flowpassword1', 'role': 'user'
        })
        assert r.status_code == 201
        client.post('/api/auth/logout')

        # Schritt 2: User loggt sich ein (U-01)
        r = login(client, 'flowuser', 'flowpassword1')
        assert r.status_code == 200
        user_data = r.get_json()
        assert user_data['username'] == 'flowuser'

        # Schritt 3: User legt Approval-Portfolio an (P-01, P-13)
        r = client.post('/api/portfolios', json={
            'name': 'My Approval Portfolio',
            'type': 'sim',
            'mode': 'approval',
            'currency': 'EUR',
            'starting_capital': 10000.0,
        })
        assert r.status_code == 201
        portfolio_id = r.get_json()['id']

        # Schritt 4: Proposal direkt in DB anlegen (simuliert 8:00-Uhr-Job)
        from models import DailyProposal, ProposedOrder, Portfolio, Account
        portfolio = Portfolio.query.get(portfolio_id)
        prop = DailyProposal(
            portfolio_id=portfolio_id,
            proposal_date=date.today(),
            status='open',
        )
        db.session.add(prop)
        db.session.flush()

        order_a = ProposedOrder(
            proposal_id=prop.id, stock_id=stock.id,
            action='BUY', shares_proposed=5.0, est_price_eur=100.0,
            score=78.0, reason='Strong buy signal', approved=True,
        )
        order_b = ProposedOrder(
            proposal_id=prop.id, stock_id=stock.id,
            action='BUY', shares_proposed=3.0, est_price_eur=100.0,
            score=66.0, reason='Moderate buy signal', approved=True,
        )
        db.session.add(order_a)
        db.session.add(order_b)
        db.session.commit()

        # Schritt 5: User sieht heutigen Proposal (API-24)
        r = client.get(f'/api/portfolios/{portfolio_id}/proposals/today')
        assert r.status_code == 200
        proposal_data = r.get_json()['proposal']
        assert proposal_data is not None
        assert len(proposal_data['orders']) == 2

        proposal_id = proposal_data['id']
        order_b_id = next(o['id'] for o in proposal_data['orders']
                          if abs(o['shares_proposed'] - 3.0) < 0.01)

        # Schritt 6: User setzt Order B auf approved=False (PR-06)
        r = client.patch(
            f'/api/proposals/{proposal_id}/orders/{order_b_id}',
            json={'approved': False}
        )
        assert r.status_code == 200
        assert r.get_json()['approved'] is False

        # Schritt 7: User führt Proposal aus (API-26, PR-07)
        r = client.post(f'/api/proposals/{proposal_id}/execute')
        assert r.status_code == 200
        result = r.get_json()

        # Schritt 8: Nur Order A wurde ausgeführt (PR-07)
        executed = [res for res in result['results'] if res['success']]
        skipped = [res for res in result['results'] if not res['success']]
        assert len(executed) == 1  # Order A
        # Order B wurde nicht einmal versucht (approved=False)
        # results enthält nur Versuche → Order B taucht nicht auf
        assert len(result['results']) == 1

        # Schritt 9: Proposal-Status = 'executed' (alle approved Orders durch) (PR-11)
        db.session.refresh(prop)
        assert prop.status == 'executed'

        # Schritt 10: executed Order kann nicht mehr geändert werden (PR-10)
        order_a_id = next(o['id'] for o in proposal_data['orders']
                          if abs(o['shares_proposed'] - 5.0) < 0.01)
        r = client.patch(
            f'/api/proposals/{proposal_id}/orders/{order_a_id}',
            json={'approved': False}
        )
        assert r.status_code == 409
