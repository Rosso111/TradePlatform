"""
Unit-Tests: Modell-Methoden (kein HTTP, direkte Objekt-Tests).
"""
import pytest
from datetime import date, datetime, timezone

from models import User, Portfolio, Account, DailyProposal, ProposedOrder


@pytest.mark.unit
class TestUserPassword:
    """U-01: Passwort-Hashing via bcrypt."""

    def test_set_and_check_correct_password(self, db):
        """U-01: korrektes Passwort wird akzeptiert."""
        u = User(username='alice', role='user')
        u.set_password('securepassword')
        assert u.check_password('securepassword') is True

    def test_wrong_password_rejected(self, db):
        """U-01: falsches Passwort wird abgelehnt."""
        u = User(username='alice', role='user')
        u.set_password('securepassword')
        assert u.check_password('wrongpassword') is False

    def test_empty_string_rejected(self, db):
        """U-01: leeres Passwort nach Hashing wird abgelehnt."""
        u = User(username='alice', role='user')
        u.set_password('securepassword')
        assert u.check_password('') is False

    def test_password_hash_stored(self, db):
        """U-01: password_hash wird befüllt."""
        u = User(username='alice', role='user')
        u.set_password('securepassword')
        assert u.password_hash is not None
        assert u.password_hash != 'securepassword'

    def test_two_users_same_password_different_hashes(self, db):
        """U-01: bcrypt salt sorgt für unterschiedliche Hashes."""
        u1 = User(username='u1', role='user')
        u2 = User(username='u2', role='user')
        u1.set_password('samepassword')
        u2.set_password('samepassword')
        assert u1.password_hash != u2.password_hash


@pytest.mark.unit
class TestUserToDict:
    """to_dict darf kein password_hash enthalten."""

    def test_to_dict_no_password_hash(self, db):
        """U-04: to_dict enthält kein password_hash."""
        u = User(username='alice', role='user', is_active=True)
        u.set_password('securepassword')
        db.session.add(u)
        db.session.commit()
        d = u.to_dict()
        assert 'password_hash' not in d

    def test_to_dict_contains_expected_fields(self, db):
        """U-04: to_dict enthält id, username, role, is_active."""
        u = User(username='alice', role='user', is_active=True)
        u.set_password('securepassword')
        db.session.add(u)
        db.session.commit()
        d = u.to_dict()
        assert 'id' in d
        assert d['username'] == 'alice'
        assert d['role'] == 'user'
        assert d['is_active'] is True


@pytest.mark.unit
class TestPortfolioToDict:
    """P-05: Portfolio to_dict enthält alle relevanten Felder."""

    def test_to_dict_fields(self, db, regular_user):
        """P-05: to_dict hat user_id, name, type, mode, status, currency."""
        p = Portfolio(
            user_id=regular_user.id,
            name='My Portfolio',
            type='sim',
            mode='auto',
            status='active',
            currency='EUR',
            starting_capital=10000.0,
        )
        db.session.add(p)
        db.session.commit()
        d = p.to_dict()
        assert d['user_id'] == regular_user.id
        assert d['name'] == 'My Portfolio'
        assert d['type'] == 'sim'
        assert d['mode'] == 'auto'
        assert d['status'] == 'active'
        assert d['currency'] == 'EUR'
        assert d['starting_capital'] == 10000.0


@pytest.mark.unit
class TestDailyProposalToDict:
    """PR-04/PR-11: DailyProposal to_dict."""

    def test_to_dict_fields(self, db, regular_user):
        """PR-04, PR-11: Proposal-Dict enthält id, portfolio_id, status."""
        p = Portfolio(user_id=regular_user.id, name='P', type='sim',
                      mode='approval', status='active', currency='EUR', starting_capital=1000.0)
        db.session.add(p)
        db.session.flush()
        prop = DailyProposal(portfolio_id=p.id, proposal_date=date.today(), status='open')
        db.session.add(prop)
        db.session.commit()
        d = prop.to_dict()
        assert d['id'] == prop.id
        assert d['portfolio_id'] == p.id
        assert d['status'] == 'open'
        assert 'proposal_date' in d


@pytest.mark.unit
class TestProposedOrderToDict:
    """PR-04: ProposedOrder to_dict enthält alle Pflichtfelder."""

    def test_to_dict_fields(self, db, proposal, stock):
        """PR-04: Order-Dict enthält symbol, action, approved, executed."""
        order = proposal.orders.first()
        d = order.to_dict()
        assert d['symbol'] == stock.symbol
        assert d['action'] == 'BUY'
        assert d['approved'] is True
        assert d['executed'] is False
        assert 'shares_proposed' in d
        assert 'est_price_eur' in d
        assert 'score' in d
        assert 'reason' in d
