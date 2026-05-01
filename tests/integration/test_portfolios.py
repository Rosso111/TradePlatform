"""
Integrationstests: Portfolio-Endpoints (routes/portfolios.py)
"""
import pytest
from models import Position, Stock, Account
from tests.conftest import login


@pytest.mark.integration
class TestListPortfolios:
    """P-06, API-09: Eigene Portfolios auflisten."""

    def test_user_sees_own_portfolios(self, user_client, portfolio):
        """P-06: User sieht nur eigene Portfolios."""
        r = user_client.get('/api/portfolios')
        assert r.status_code == 200
        data = r.get_json()
        assert len(data) == 1
        assert data[0]['name'] == 'Test Portfolio'

    def test_user_does_not_see_other_users_portfolios(self, user_client, admin_portfolio):
        """P-06: User sieht keine Portfolios anderer User."""
        r = user_client.get('/api/portfolios')
        assert r.status_code == 200
        ids = [p['id'] for p in r.get_json()]
        assert admin_portfolio.id not in ids

    def test_unauthenticated_cannot_list(self, client):
        """U-04: Nicht eingeloggt → 401."""
        r = client.get('/api/portfolios')
        assert r.status_code == 401


@pytest.mark.integration
class TestCreatePortfolio:
    """P-01, API-10: Portfolio anlegen."""

    def test_user_creates_portfolio(self, user_client):
        """P-01: User legt Portfolio an → 201."""
        r = user_client.post('/api/portfolios', json={
            'name': 'New Portfolio',
            'type': 'sim',
            'mode': 'auto',
            'currency': 'EUR',
            'starting_capital': 5000.0,
        })
        assert r.status_code == 201
        data = r.get_json()
        assert data['name'] == 'New Portfolio'
        assert data['status'] == 'active'

    def test_missing_required_name(self, user_client):
        """P-01: Name fehlt → 400."""
        r = user_client.post('/api/portfolios', json={})
        assert r.status_code == 400

    def test_invalid_type(self, user_client):
        """P-01: Ungültiger Typ → 400."""
        r = user_client.post('/api/portfolios', json={
            'name': 'P', 'type': 'invalid', 'mode': 'auto',
            'currency': 'EUR', 'starting_capital': 1000.0,
        })
        assert r.status_code == 400

    def test_invalid_mode(self, user_client):
        """P-01: Ungültiger Modus → 400."""
        r = user_client.post('/api/portfolios', json={
            'name': 'P', 'type': 'sim', 'mode': 'magic',
            'currency': 'EUR', 'starting_capital': 1000.0,
        })
        assert r.status_code == 400


@pytest.mark.integration
class TestGetPortfolio:
    """P-06, API-11: Portfolio-Detail."""

    def test_owner_gets_portfolio(self, user_client, portfolio):
        """P-06: Eigentümer bekommt Portfolio-Detail."""
        r = user_client.get(f'/api/portfolios/{portfolio.id}')
        assert r.status_code == 200
        assert r.get_json()['id'] == portfolio.id

    def test_other_user_gets_403(self, user_client, admin_portfolio):
        """P-06: Fremdes Portfolio → 403."""
        r = user_client.get(f'/api/portfolios/{admin_portfolio.id}')
        assert r.status_code == 403

    def test_nonexistent_portfolio_gets_404(self, user_client):
        """API-11: Nicht existentes Portfolio → 404."""
        r = user_client.get('/api/portfolios/99999')
        assert r.status_code == 404


@pytest.mark.integration
class TestUpdatePortfolio:
    """API-12: Portfolio bearbeiten."""

    def test_owner_updates_name(self, user_client, portfolio):
        """API-12: Eigentümer kann Name ändern."""
        r = user_client.put(f'/api/portfolios/{portfolio.id}', json={'name': 'Renamed'})
        assert r.status_code == 200
        assert r.get_json()['name'] == 'Renamed'

    def test_immutable_field_type_rejected(self, user_client, portfolio):
        """API-12: Unveränderlicher Typ → 400."""
        r = user_client.put(f'/api/portfolios/{portfolio.id}', json={'type': 'ibkr_paper'})
        assert r.status_code == 400

    def test_immutable_field_currency_rejected(self, user_client, portfolio):
        """API-12: Unveränderliche Währung → 400."""
        r = user_client.put(f'/api/portfolios/{portfolio.id}', json={'currency': 'USD'})
        assert r.status_code == 400

    def test_immutable_field_starting_capital_rejected(self, user_client, portfolio):
        """API-12: Unveränderliches Startkapital → 400."""
        r = user_client.put(f'/api/portfolios/{portfolio.id}', json={'starting_capital': 99999.0})
        assert r.status_code == 400


@pytest.mark.integration
class TestTogglePortfolioStatus:
    """P-02, API-13: Portfolio aktivieren/deaktivieren."""

    def test_deactivate_portfolio(self, user_client, portfolio):
        """P-02: Aktives Portfolio deaktivieren."""
        # Zweites Portfolio anlegen damit Deaktivierung möglich ist
        user_client.post('/api/portfolios', json={
            'name': 'Second', 'type': 'sim', 'mode': 'auto',
            'currency': 'EUR', 'starting_capital': 1000.0,
        })
        r = user_client.patch(f'/api/portfolios/{portfolio.id}/status')
        assert r.status_code == 200
        assert r.get_json()['status'] == 'inactive'

    def test_cannot_deactivate_last_active_portfolio(self, user_client, portfolio):
        """P-02: Letztes aktives Portfolio kann nicht deaktiviert werden → 400."""
        r = user_client.patch(f'/api/portfolios/{portfolio.id}/status')
        assert r.status_code == 400


@pytest.mark.integration
class TestDeletePortfolio:
    """P-04, API-14: Portfolio löschen."""

    def test_delete_empty_portfolio(self, db, user_client, portfolio, regular_user):
        """P-04: Portfolio ohne Positionen kann gelöscht werden → 200."""
        from models import Portfolio, Account
        # Zweites Portfolio nötig — letztes darf nicht gelöscht werden
        second = Portfolio(user_id=regular_user.id, name='ToDelete', type='sim',
                          mode='auto', status='inactive', currency='EUR', starting_capital=1000.0)
        db.session.add(second)
        db.session.flush()
        db.session.add(Account(portfolio_id=second.id, cash_eur=1000.0, equity_eur=1000.0))
        db.session.commit()
        r = user_client.delete(f'/api/portfolios/{second.id}')
        assert r.status_code == 200

    def test_cannot_delete_portfolio_with_positions(self, db, user_client, portfolio, regular_user, stock):
        """P-04: Portfolio mit offenen Positionen kann nicht gelöscht werden → 400."""
        from models import Portfolio, Account
        # Zweites Portfolio mit Positionen anlegen
        second = Portfolio(user_id=regular_user.id, name='WithPositions', type='sim',
                          mode='auto', status='inactive', currency='EUR', starting_capital=1000.0)
        db.session.add(second)
        db.session.flush()
        db.session.add(Account(portfolio_id=second.id, cash_eur=1000.0, equity_eur=1000.0))
        pos = Position(
            portfolio_id=second.id, stock_id=stock.id,
            shares=10.0, entry_price=100.0, entry_price_eur=100.0,
            cost_eur=1000.0,
        )
        db.session.add(pos)
        db.session.commit()
        r = user_client.delete(f'/api/portfolios/{second.id}')
        assert r.status_code == 400

    def test_other_user_cannot_delete(self, user_client, admin_portfolio):
        """P-06: Fremdes Portfolio löschen → 403."""
        r = user_client.delete(f'/api/portfolios/{admin_portfolio.id}')
        assert r.status_code == 403


@pytest.mark.integration
class TestAdminPortfolioAccess:
    """P-07: Admin sieht alle Portfolios."""

    def test_admin_sees_all_portfolios(self, admin_client, portfolio, admin_portfolio):
        """P-07: Admin sieht Portfolios aller User."""
        r = admin_client.get('/api/portfolios')
        assert r.status_code == 200
        ids = [p['id'] for p in r.get_json()]
        assert portfolio.id in ids
        assert admin_portfolio.id in ids
