"""Unit-Tests für die yfinance-Drossel in services/data_fetcher.py — keine DB nötig."""
from datetime import datetime, timedelta

import pytest

import services.data_fetcher as df_mod


@pytest.fixture(autouse=True)
def reset_state(monkeypatch):
    monkeypatch.setattr(df_mod, '_last_price_update', None)
    monkeypatch.setattr(df_mod, '_fx_cache', None)
    # braucht app/DB — hier geht es nur um die Drossel
    monkeypatch.setattr(df_mod, '_with_position_stocks', lambda app, universe: universe)


def _stub_store(calls):
    def stub(app, universe, days=5):
        calls.append(days)
    return stub


# ── update_prices_incremental Drossel ────────────────────────────────────────

def test_first_update_runs(monkeypatch):
    calls = []
    monkeypatch.setattr(df_mod, 'store_prices_to_db', _stub_store(calls))
    assert df_mod.update_prices_incremental(None, []) is True
    assert calls == [5]


def test_second_update_within_interval_is_throttled(monkeypatch):
    calls = []
    monkeypatch.setattr(df_mod, 'store_prices_to_db', _stub_store(calls))
    assert df_mod.update_prices_incremental(None, []) is True
    assert df_mod.update_prices_incremental(None, []) is False
    assert len(calls) == 1


def test_force_bypasses_throttle(monkeypatch):
    calls = []
    monkeypatch.setattr(df_mod, 'store_prices_to_db', _stub_store(calls))
    df_mod.update_prices_incremental(None, [])
    assert df_mod.update_prices_incremental(None, [], force=True) is True
    assert len(calls) == 2


def test_update_runs_again_after_interval(monkeypatch):
    calls = []
    monkeypatch.setattr(df_mod, 'store_prices_to_db', _stub_store(calls))
    monkeypatch.setattr(df_mod, '_last_price_update',
                        datetime.now() - timedelta(hours=25))
    assert df_mod.update_prices_incremental(None, []) is True
    assert len(calls) == 1


def test_failed_update_does_not_set_timestamp(monkeypatch):
    def boom(app, universe, days=5):
        raise RuntimeError('yfinance down')
    monkeypatch.setattr(df_mod, 'store_prices_to_db', boom)
    with pytest.raises(RuntimeError):
        df_mod.update_prices_incremental(None, [])
    # Fehlschlag zählt nicht als Update — nächster Aufruf versucht es erneut
    assert df_mod._last_price_update is None
    calls = []
    monkeypatch.setattr(df_mod, 'store_prices_to_db', _stub_store(calls))
    assert df_mod.update_prices_incremental(None, []) is True


# ── fetch_exchange_rates Cache ───────────────────────────────────────────────

def test_fx_cache_hit_returns_cached_rates(monkeypatch):
    cached = {'EUR': 1.0, 'USD': 9.99}
    monkeypatch.setattr(df_mod, '_fx_cache', (datetime.now(), cached))
    assert df_mod.fetch_exchange_rates() == cached


def test_fx_cache_returns_copy(monkeypatch):
    cached = {'EUR': 1.0, 'USD': 9.99}
    monkeypatch.setattr(df_mod, '_fx_cache', (datetime.now(), cached))
    result = df_mod.fetch_exchange_rates()
    result['USD'] = 0.0
    assert df_mod.fetch_exchange_rates()['USD'] == 9.99
