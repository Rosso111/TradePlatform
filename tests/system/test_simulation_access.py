"""
Zugriffskontrolle auf Simulations-Endpoints (PROBE-9) + Query-Param-Validierung (REED-6).
"""
from datetime import date

import pytest

from models import SimulationRun
from tests.conftest import login


@pytest.fixture
def own_run(db, regular_user):
    run = SimulationRun(
        name='Own Run', status='completed', user_id=regular_user.id,
        start_date=date(2025, 1, 1), end_date=date(2025, 6, 30),
    )
    db.session.add(run)
    db.session.commit()
    return run


@pytest.fixture
def foreign_run(db, second_user):
    run = SimulationRun(
        name='Foreign Run', status='completed', user_id=second_user.id,
        start_date=date(2025, 1, 1), end_date=date(2025, 6, 30),
    )
    db.session.add(run)
    db.session.commit()
    return run


class TestSimulationOwnership:
    def test_owner_can_read_own_run(self, user_client, own_run):
        resp = user_client.get(f'/api/simulations/{own_run.id}')
        assert resp.status_code == 200

    def test_non_owner_gets_403(self, user_client, foreign_run):
        resp = user_client.get(f'/api/simulations/{foreign_run.id}')
        assert resp.status_code == 403

    def test_non_owner_gets_403_on_subresources(self, user_client, foreign_run):
        for sub in ('equity', 'trades', 'positions', 'decisions', 'metrics', 'benchmark'):
            resp = user_client.get(f'/api/simulations/{foreign_run.id}/{sub}')
            assert resp.status_code == 403, f'/{sub} nicht geschützt'

    def test_non_owner_cannot_delete(self, user_client, foreign_run):
        resp = user_client.delete(f'/api/simulations/{foreign_run.id}')
        assert resp.status_code == 403

    def test_admin_can_read_foreign_run(self, admin_client, foreign_run):
        resp = admin_client.get(f'/api/simulations/{foreign_run.id}')
        assert resp.status_code == 200

    def test_list_hides_foreign_runs(self, client, regular_user, own_run, foreign_run):
        login(client, 'testuser', 'userpassword1')
        resp = client.get('/api/simulations')
        assert resp.status_code == 200
        ids = [row['id'] for row in resp.get_json()]
        assert own_run.id in ids
        assert foreign_run.id not in ids

    def test_list_shows_all_for_admin(self, client, admin_user, own_run, foreign_run):
        login(client, 'admin', 'adminpassword1')
        resp = client.get('/api/simulations')
        ids = [row['id'] for row in resp.get_json()]
        assert own_run.id in ids and foreign_run.id in ids


class TestQueryIntValidation:
    def test_invalid_limit_returns_400(self, user_client, own_run):
        resp = user_client.get(f'/api/simulations/{own_run.id}/trades?limit=abc')
        assert resp.status_code == 400

    def test_limit_is_clamped(self, user_client, own_run):
        resp = user_client.get(f'/api/simulations/{own_run.id}/trades?limit=999999')
        assert resp.status_code == 200

    def test_valid_limit_passes(self, user_client, own_run):
        resp = user_client.get(f'/api/simulations/{own_run.id}/decisions?limit=10')
        assert resp.status_code == 200
