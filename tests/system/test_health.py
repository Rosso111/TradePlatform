"""Health-Endpoint für systemd-Watchdog/Monitoring (Felix-8)."""


def test_health_ok_without_login(client, db):
    resp = client.get('/health')
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['status'] == 'ok'
    assert data['checks']['db'] == 'ok'


def test_health_reports_scheduler_state(client, db):
    data = client.get('/health').get_json()
    assert data['checks']['scheduler'] in ('running', 'stopped')
