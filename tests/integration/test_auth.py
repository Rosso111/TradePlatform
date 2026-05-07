"""
Integrationstests: Auth-Endpoints (routes/auth.py)
"""
import pytest
from tests.conftest import login


@pytest.mark.integration
class TestLogin:
    """U-01, U-02: Login-Verhalten."""

    def test_login_correct_credentials(self, client, regular_user):
        """U-01: Korrekter Login gibt 200 + User-Dict zurück."""
        r = login(client, 'testuser', 'userpassword1')
        assert r.status_code == 200
        data = r.get_json()
        assert data['username'] == 'testuser'
        assert 'password_hash' not in data

    def test_login_wrong_password(self, client, regular_user):
        """U-01: Falsches Passwort → 401 mit einheitlicher Fehlermeldung."""
        r = login(client, 'testuser', 'wrongpassword')
        assert r.status_code == 401
        assert 'error' in r.get_json()

    def test_login_nonexistent_user(self, client):
        """U-01: Nicht existenter User → 401, gleiche Fehlermeldung wie falsches PW."""
        r = login(client, 'ghostuser', 'anypassword')
        assert r.status_code == 401
        assert 'error' in r.get_json()

    def test_login_same_error_for_wrong_pw_and_missing_user(self, client, regular_user):
        """U-01: Timing-Attack-Schutz — gleiche Fehlermeldung bei falschem PW und fehlendem User."""
        r_wrong_pw = login(client, 'testuser', 'wrongpassword')
        r_no_user = login(client, 'ghostuser', 'anypassword')
        assert r_wrong_pw.get_json()['error'] == r_no_user.get_json()['error']

    def test_login_inactive_user(self, client, db, regular_user):
        """U-11: Deaktivierter User kann sich nicht einloggen."""
        regular_user.is_active = False
        db.session.commit()
        r = login(client, 'testuser', 'userpassword1')
        assert r.status_code == 401

    def test_login_inactive_same_error_as_wrong_pw(self, client, db, regular_user):
        """U-11: Deaktivierter User bekommt gleiche Fehlermeldung (kein Informationsleck)."""
        regular_user.is_active = False
        db.session.commit()
        r_inactive = login(client, 'testuser', 'userpassword1')
        r_wrong = login(client, 'testuser', 'wrongpassword')
        assert r_inactive.get_json()['error'] == r_wrong.get_json()['error']

    def test_login_missing_username(self, client):
        """U-01: Fehlender Username → 400."""
        r = client.post('/api/auth/login', json={'password': 'userpassword1'})
        assert r.status_code == 400

    def test_login_missing_password(self, client, regular_user):
        """U-01: Fehlendes Passwort → 400."""
        r = client.post('/api/auth/login', json={'username': 'testuser'})
        assert r.status_code == 400

    def test_login_empty_body(self, client):
        """U-01: Leerer Request-Body → 400."""
        r = client.post('/api/auth/login', json={})
        assert r.status_code == 400


@pytest.mark.integration
class TestLogout:
    """U-02: Logout."""

    def test_logout_after_login(self, client, regular_user):
        """U-02: Logout gibt 200."""
        login(client, 'testuser', 'userpassword1')
        r = client.post('/api/auth/logout')
        assert r.status_code == 200

    def test_logout_without_login(self, client):
        """U-02: Logout ohne aktive Session gibt 200 (idempotent)."""
        r = client.post('/api/auth/logout')
        assert r.status_code == 200

    def test_me_after_logout_returns_401(self, client, regular_user):
        """U-04: Nach Logout ist /me nicht mehr zugänglich."""
        login(client, 'testuser', 'userpassword1')
        client.post('/api/auth/logout')
        r = client.get('/api/auth/me')
        assert r.status_code == 401


@pytest.mark.integration
class TestMe:
    """U-04: /api/auth/me"""

    def test_me_returns_user_with_portfolios(self, client, regular_user, portfolio):
        """U-04: /me gibt eingeloggten User + seine Portfolios zurück."""
        login(client, 'testuser', 'userpassword1')
        r = client.get('/api/auth/me')
        assert r.status_code == 200
        data = r.get_json()
        assert data['username'] == 'testuser'
        assert 'portfolios' in data
        assert isinstance(data['portfolios'], list)

    def test_me_unauthenticated(self, client):
        """U-04: Nicht eingeloggter Zugriff → 401."""
        r = client.get('/api/auth/me')
        assert r.status_code == 401
