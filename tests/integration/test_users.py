"""
Integrationstests: User-Management-Endpoints (routes/users.py)
"""
import pytest
from tests.conftest import login


@pytest.mark.integration
class TestListUsers:
    """U-06, API-04: User auflisten."""

    def test_admin_can_list_users(self, admin_client, regular_user):
        """U-06: Admin sieht alle User."""
        r = admin_client.get('/api/users')
        assert r.status_code == 200
        usernames = [u['username'] for u in r.get_json()]
        assert 'testuser' in usernames

    def test_regular_user_cannot_list_users(self, user_client):
        """RBAC: Normaler User → 403."""
        r = user_client.get('/api/users')
        assert r.status_code == 403

    def test_unauthenticated_cannot_list_users(self, client):
        """U-04: Nicht eingeloggt → 401."""
        r = client.get('/api/users')
        assert r.status_code == 401


@pytest.mark.integration
class TestCreateUser:
    """U-06, U-10: User anlegen."""

    def test_admin_creates_user(self, admin_client):
        """U-06, U-10: Admin legt neuen User an → 201."""
        r = admin_client.post('/api/users', json={
            'username': 'newuser', 'password': 'validpassword1', 'role': 'user'
        })
        assert r.status_code == 201
        data = r.get_json()
        assert data['username'] == 'newuser'
        assert data['role'] == 'user'
        assert 'password_hash' not in data

    def test_create_user_without_self_registration(self, client):
        """U-10: Kein Self-Registration — ohne Admin-Login → 401."""
        r = client.post('/api/users', json={
            'username': 'selfregister', 'password': 'validpassword1'
        })
        assert r.status_code == 401

    def test_regular_user_cannot_create_user(self, user_client):
        """U-10: Normaler User kann keinen User anlegen → 403."""
        r = user_client.post('/api/users', json={
            'username': 'newone', 'password': 'validpassword1'
        })
        assert r.status_code == 403

    def test_create_user_duplicate_username(self, admin_client, regular_user):
        """U-06: Doppelter Username → 409."""
        r = admin_client.post('/api/users', json={
            'username': 'testuser', 'password': 'validpassword1'
        })
        assert r.status_code == 409

    def test_create_user_missing_username(self, admin_client):
        """U-06: Fehlender Username → 400."""
        r = admin_client.post('/api/users', json={'password': 'validpassword1'})
        assert r.status_code == 400

    def test_create_user_invalid_role(self, admin_client):
        """U-06: Ungültige Rolle → 400."""
        r = admin_client.post('/api/users', json={
            'username': 'x', 'password': 'validpassword1', 'role': 'superuser'
        })
        assert r.status_code == 400

    @pytest.mark.parametrize('password,expected_status', [
        ('short',  400),    # 5 Zeichen — zu kurz
        ('123456789', 400), # 9 Zeichen — ein unter dem Minimum
        ('1234567890', 201), # 10 Zeichen — genau das Minimum
        ('averylongpassword123', 201),  # deutlich über dem Minimum
    ])
    def test_password_length_validation(self, admin_client, password, expected_status):
        """U-06: Passwort muss mindestens 10 Zeichen haben (Grenzwertanalyse)."""
        r = admin_client.post('/api/users', json={
            'username': f'user_{len(password)}', 'password': password
        })
        assert r.status_code == expected_status


@pytest.mark.integration
class TestToggleUserStatus:
    """U-07, API-07: User aktivieren/deaktivieren."""

    def test_admin_deactivates_user(self, admin_client, regular_user):
        """U-07: Admin deaktiviert User → is_active=False."""
        r = admin_client.patch(f'/api/users/{regular_user.id}/status')
        assert r.status_code == 200
        assert r.get_json()['is_active'] is False

    def test_admin_reactivates_user(self, admin_client, db, regular_user):
        """U-07: Admin aktiviert deaktivierten User."""
        regular_user.is_active = False
        db.session.commit()
        r = admin_client.patch(f'/api/users/{regular_user.id}/status')
        assert r.status_code == 200
        assert r.get_json()['is_active'] is True

    def test_admin_cannot_deactivate_self(self, admin_client, admin_user):
        """U-07: Admin kann sich nicht selbst deaktivieren → 400."""
        r = admin_client.patch(f'/api/users/{admin_user.id}/status')
        assert r.status_code == 400

    def test_deactivated_user_cannot_login(self, admin_client, client, db, regular_user):
        """U-11: Deaktivierter User kann sich nicht mehr einloggen."""
        admin_client.patch(f'/api/users/{regular_user.id}/status')
        r = login(client, 'testuser', 'userpassword1')
        assert r.status_code == 401


@pytest.mark.integration
class TestChangePassword:
    """U-08, U-09: Passwort ändern."""

    def test_user_changes_own_password(self, user_client, regular_user):
        """U-09: User ändert eigenes Passwort mit altem Passwort."""
        r = user_client.put(f'/api/users/{regular_user.id}/password', json={
            'old_password': 'userpassword1',
            'new_password': 'newpassword123',
        })
        assert r.status_code == 200

    def test_user_wrong_old_password(self, user_client, regular_user):
        """U-09: Falsches altes Passwort → 401."""
        r = user_client.put(f'/api/users/{regular_user.id}/password', json={
            'old_password': 'wrongoldpass',
            'new_password': 'newpassword123',
        })
        assert r.status_code == 401

    def test_user_cannot_change_other_users_password(self, user_client, admin_user):
        """U-09: User kann fremdes Passwort nicht ändern → 403."""
        r = user_client.put(f'/api/users/{admin_user.id}/password', json={
            'new_password': 'newpassword123',
        })
        assert r.status_code == 403

    def test_admin_resets_password_without_old(self, admin_client, regular_user):
        """U-08: Admin setzt Passwort zurück ohne altes Passwort."""
        r = admin_client.put(f'/api/users/{regular_user.id}/password', json={
            'new_password': 'resetpassword1',
        })
        assert r.status_code == 200

    def test_new_password_too_short(self, user_client, regular_user):
        """U-09: Neues Passwort unter Minimum → 400."""
        r = user_client.put(f'/api/users/{regular_user.id}/password', json={
            'old_password': 'userpassword1',
            'new_password': 'short',
        })
        assert r.status_code == 400

    def test_missing_new_password(self, user_client, regular_user):
        """U-09: Fehlendes neues Passwort → 400."""
        r = user_client.put(f'/api/users/{regular_user.id}/password', json={
            'old_password': 'userpassword1',
        })
        assert r.status_code == 400
