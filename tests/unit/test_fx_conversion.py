"""Unit-Tests für die FX-Umrechnung in services/data_fetcher.py — keine DB nötig."""
from datetime import date

import pandas as pd

from services.data_fetcher import FxLookup, close_to_eur


def _history(**series):
    return {
        currency: pd.Series(
            list(values.values()),
            index=list(values.keys()),
        ).sort_index()
        for currency, values in series.items()
    }


# ── FxLookup ──────────────────────────────────────────────────────────────────

def test_rate_exact_date():
    fx = FxLookup(_history(USD={date(2026, 1, 5): 1.10, date(2026, 1, 6): 1.12}), {})
    assert fx.rate('USD', date(2026, 1, 6)) == 1.12


def test_rate_weekend_uses_last_known():
    # Samstag → Kurs vom Freitag
    fx = FxLookup(_history(USD={date(2026, 1, 9): 1.10, date(2026, 1, 12): 1.15}), {})
    assert fx.rate('USD', date(2026, 1, 10)) == 1.10


def test_rate_before_history_falls_back():
    fx = FxLookup(_history(USD={date(2026, 1, 5): 1.10}), {'USD': 1.08})
    assert fx.rate('USD', date(2025, 12, 1)) == 1.08


def test_rate_unknown_currency_falls_back():
    fx = FxLookup({}, {'JPY': 163.0})
    assert fx.rate('JPY', date(2026, 1, 5)) == 163.0


def test_rate_eur_is_always_one():
    fx = FxLookup({}, {})
    assert fx.rate('EUR', date(2026, 1, 5)) == 1.0


def test_rate_missing_fallback_defaults_to_one():
    fx = FxLookup({}, {})
    assert fx.rate('XXX', date(2026, 1, 5)) == 1.0


# ── close_to_eur ──────────────────────────────────────────────────────────────

def test_close_to_eur_regular_symbol():
    assert abs(close_to_eur('AAPL', 110.0, 1.10) - 100.0) < 1e-9


def test_close_to_eur_lse_symbol_divides_gbx():
    # 2500 GBX = 25 GBP; bei 0.85 GBP/EUR → ~29.41 EUR
    assert abs(close_to_eur('SHEL.L', 2500.0, 0.85) - 25.0 / 0.85) < 1e-9


def test_close_to_eur_zero_rate_keeps_value():
    assert close_to_eur('AAPL', 110.0, 0.0) == 110.0
    assert close_to_eur('SHEL.L', 2500.0, 0.0) == 25.0
