"""
Unit-Tests: services/proposal_generator.py
"""
import pytest
from datetime import date, timedelta

from models import DailyProposal, Portfolio, Account
from services.proposal_generator import expire_stale_proposals


@pytest.mark.unit
class TestExpireStaleProposals:
    """PR-09: Veraltete Proposals werden auf 'expired' gesetzt."""

    def _make_proposal(self, db, portfolio_id, proposal_date, status):
        p = DailyProposal(
            portfolio_id=portfolio_id,
            proposal_date=proposal_date,
            status=status,
        )
        db.session.add(p)
        db.session.commit()
        return p

    def _make_portfolio(self, db, user):
        p = Portfolio(user_id=user.id, name='EP', type='sim', mode='approval',
                      status='active', currency='EUR', starting_capital=1000.0)
        db.session.add(p)
        db.session.flush()
        db.session.add(Account(portfolio_id=p.id, cash_eur=1000.0, equity_eur=1000.0))
        db.session.commit()
        return p

    def test_open_yesterday_becomes_expired(self, app, db, regular_user):
        """PR-09: 'open' Proposal von gestern → 'expired'."""
        port = self._make_portfolio(db, regular_user)
        yesterday = date.today() - timedelta(days=1)
        prop = self._make_proposal(db, port.id, yesterday, 'open')

        count = expire_stale_proposals(app)

        db.session.refresh(prop)
        assert prop.status == 'expired'
        assert count == 1

    def test_partially_executed_yesterday_becomes_expired(self, app, db, regular_user):
        """PR-09: 'partially_executed' Proposal von gestern → 'expired'."""
        port = self._make_portfolio(db, regular_user)
        yesterday = date.today() - timedelta(days=1)
        prop = self._make_proposal(db, port.id, yesterday, 'partially_executed')

        expire_stale_proposals(app)

        db.session.refresh(prop)
        assert prop.status == 'expired'

    def test_open_today_stays_open(self, app, db, regular_user):
        """PR-09: 'open' Proposal von heute bleibt 'open'."""
        port = self._make_portfolio(db, regular_user)
        prop = self._make_proposal(db, port.id, date.today(), 'open')

        expire_stale_proposals(app)

        db.session.refresh(prop)
        assert prop.status == 'open'

    def test_executed_proposal_unchanged(self, app, db, regular_user):
        """PR-09: 'executed' Proposals werden nicht angefasst."""
        port = self._make_portfolio(db, regular_user)
        yesterday = date.today() - timedelta(days=1)
        prop = self._make_proposal(db, port.id, yesterday, 'executed')

        expire_stale_proposals(app)

        db.session.refresh(prop)
        assert prop.status == 'executed'

    def test_returns_count_of_expired(self, app, db, regular_user):
        """PR-09: Rückgabewert = Anzahl abgelaufener Proposals."""
        port = self._make_portfolio(db, regular_user)
        yesterday = date.today() - timedelta(days=1)
        two_days_ago = date.today() - timedelta(days=2)
        self._make_proposal(db, port.id, yesterday, 'open')
        self._make_proposal(db, port.id, two_days_ago, 'open')

        count = expire_stale_proposals(app)
        assert count == 2

    def test_no_stale_proposals_returns_zero(self, app, db):
        """PR-09: Keine veralteten Proposals → Rückgabe 0."""
        count = expire_stale_proposals(app)
        assert count == 0


@pytest.mark.unit
class TestGenerateDailyProposalsSmoke:
    """PR-03: Smoke-Test — läuft ohne Exception bei leerem DB."""

    def test_runs_without_exception_on_empty_db(self, app, db):
        """PR-03: Keine Exception bei leerer DB."""
        from services.proposal_generator import generate_daily_proposals
        count = generate_daily_proposals(app)
        assert count == 0
