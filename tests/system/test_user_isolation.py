"""
Systemtest: Multi-User-Isolation (G-02, P-06)
Stellt sicher dass User A niemals Daten von User B sehen oder manipulieren kann.
"""
import pytest
from datetime import date
from tests.conftest import login


@pytest.mark.system
class TestUserIsolation:
    """
    G-02, P-06: Verschiedene User sind vollständig voneinander isoliert.
    """

    def test_user_cannot_see_other_users_portfolio(self, client, regular_user, second_user, db):
        """P-06: User A sieht kein Portfolio von User B."""
        from models import Portfolio, Account

        # User B legt Portfolio an
        b_portfolio = Portfolio(
            user_id=second_user.id, name='User B Portfolio',
            type='sim', mode='auto', status='active', currency='EUR', starting_capital=1000.0,
        )
        db.session.add(b_portfolio)
        db.session.flush()
        db.session.add(Account(portfolio_id=b_portfolio.id, cash_eur=1000.0, equity_eur=1000.0))
        db.session.commit()

        # User A einloggen
        login(client, 'testuser', 'userpassword1')
        r = client.get('/api/portfolios')
        assert r.status_code == 200
        ids = [p['id'] for p in r.get_json()]
        assert b_portfolio.id not in ids

    def test_user_cannot_access_other_users_portfolio_directly(self, client, regular_user, second_user, db):
        """P-06: User A kann Portfolio von User B nicht direkt abrufen → 403."""
        from models import Portfolio, Account

        b_portfolio = Portfolio(
            user_id=second_user.id, name='B Private', type='sim',
            mode='auto', status='active', currency='EUR', starting_capital=1000.0,
        )
        db.session.add(b_portfolio)
        db.session.flush()
        db.session.add(Account(portfolio_id=b_portfolio.id, cash_eur=1000.0, equity_eur=1000.0))
        db.session.commit()

        login(client, 'testuser', 'userpassword1')
        r = client.get(f'/api/portfolios/{b_portfolio.id}')
        assert r.status_code == 403

    def test_user_cannot_modify_other_users_portfolio(self, client, regular_user, second_user, db):
        """P-06: User A kann Portfolio von User B nicht bearbeiten → 403."""
        from models import Portfolio, Account

        b_portfolio = Portfolio(
            user_id=second_user.id, name='B Portfolio', type='sim',
            mode='auto', status='active', currency='EUR', starting_capital=1000.0,
        )
        db.session.add(b_portfolio)
        db.session.flush()
        db.session.add(Account(portfolio_id=b_portfolio.id, cash_eur=1000.0, equity_eur=1000.0))
        db.session.commit()

        login(client, 'testuser', 'userpassword1')
        r = client.put(f'/api/portfolios/{b_portfolio.id}', json={'name': 'Hijacked'})
        assert r.status_code == 403

    def test_user_cannot_execute_proposal_of_other_user(self, client, regular_user, second_user, db, stock):
        """P-06, API-26: User A kann Proposal von User B nicht ausführen → 403."""
        from models import Portfolio, Account, DailyProposal, ProposedOrder

        b_portfolio = Portfolio(
            user_id=second_user.id, name='B Approval', type='sim',
            mode='approval', status='active', currency='EUR', starting_capital=5000.0,
        )
        db.session.add(b_portfolio)
        db.session.flush()
        db.session.add(Account(portfolio_id=b_portfolio.id, cash_eur=5000.0, equity_eur=5000.0))
        db.session.flush()

        prop = DailyProposal(portfolio_id=b_portfolio.id, proposal_date=date.today(), status='open')
        db.session.add(prop)
        db.session.flush()
        db.session.add(ProposedOrder(
            proposal_id=prop.id, stock_id=stock.id, action='BUY',
            shares_proposed=5.0, est_price_eur=100.0, score=70.0, approved=True,
        ))
        db.session.commit()

        # User A versucht Proposal von User B auszuführen
        login(client, 'testuser', 'userpassword1')
        r = client.post(f'/api/proposals/{prop.id}/execute')
        assert r.status_code == 403

    def test_admin_can_access_all_portfolios(self, client, admin_user, regular_user, db):
        """P-07: Admin sieht alle Portfolios aller User."""
        from models import Portfolio, Account

        user_portfolio = Portfolio(
            user_id=regular_user.id, name='User Portfolio', type='sim',
            mode='auto', status='active', currency='EUR', starting_capital=1000.0,
        )
        db.session.add(user_portfolio)
        db.session.flush()
        db.session.add(Account(portfolio_id=user_portfolio.id, cash_eur=1000.0, equity_eur=1000.0))
        db.session.commit()

        login(client, 'admin', 'adminpassword1')
        r = client.get('/api/portfolios')
        assert r.status_code == 200
        ids = [p['id'] for p in r.get_json()]
        assert user_portfolio.id in ids

    def test_two_users_independent_portfolios(self, client, regular_user, second_user, db):
        """G-02: Jeder User verwaltet seine eigenen Portfolios unabhängig."""
        from models import Portfolio, Account

        for user in [regular_user, second_user]:
            p = Portfolio(user_id=user.id, name=f'{user.username} P', type='sim',
                         mode='auto', status='active', currency='EUR', starting_capital=1000.0)
            db.session.add(p)
            db.session.flush()
            db.session.add(Account(portfolio_id=p.id, cash_eur=1000.0, equity_eur=1000.0))
        db.session.commit()

        # User A sieht genau 1 Portfolio
        login(client, regular_user.username, 'userpassword1')
        r = client.get('/api/portfolios')
        assert len(r.get_json()) == 1
        client.post('/api/auth/logout')

        # User B sieht genau 1 Portfolio
        login(client, second_user.username, 'otherpassword1')
        r = client.get('/api/portfolios')
        assert len(r.get_json()) == 1
