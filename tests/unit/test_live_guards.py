"""Daten-Guards im Live-Zyklus: Re-Buy-Cooldown, Split-Verdacht, Stale-Preise."""
from datetime import date, datetime, timedelta, timezone

import pytest

from models import Trade
from services.live_runner import (
    blocked_by_rebuy_cooldown, is_price_jump_suspicious, is_price_stale,
)


class TestPriceJumpSuspicious:
    def test_split_like_drop_is_suspicious(self):
        # KLAC-Fall: 1650 → 215 nach unbehandeltem Split
        assert is_price_jump_suspicious(215.0, 1650.0) is True

    def test_normal_daily_move_is_fine(self):
        assert is_price_jump_suspicious(95.0, 100.0) is False

    def test_threshold_is_configurable(self):
        assert is_price_jump_suspicious(70.0, 100.0, threshold=0.25) is True
        assert is_price_jump_suspicious(70.0, 100.0, threshold=0.35) is False

    def test_missing_prev_close_is_not_suspicious(self):
        assert is_price_jump_suspicious(100.0, 0.0) is False
        assert is_price_jump_suspicious(100.0, None) is False


class TestPriceStale:
    def test_recent_price_not_stale(self):
        assert is_price_stale(date.today() - timedelta(days=2)) is False

    def test_old_price_is_stale(self):
        assert is_price_stale(date.today() - timedelta(days=8)) is True


@pytest.fixture
def stop_loss_trade(db, portfolio, stock):
    t = Trade(
        portfolio_id=portfolio.id, stock_id=stock.id, action='SELL',
        shares=10.0, price=100.0, price_eur=100.0, total_eur=1000.0,
        pnl_eur=-150.0, reason='IBKR Fill @ 100.00 — Stop-Loss ausgelöst',
        executed_at=datetime.now(timezone.utc) - timedelta(days=3),
    )
    db.session.add(t)
    db.session.commit()
    return t


class TestRebuyCooldown:
    def test_blocked_shortly_after_stop_loss(self, db, portfolio, stock, stop_loss_trade):
        assert blocked_by_rebuy_cooldown(portfolio.id, stock.id, score=55, buy_threshold=50) is True

    def test_allowed_with_strong_score(self, db, portfolio, stock, stop_loss_trade):
        # Score >= threshold + margin überstimmt den Cooldown
        assert blocked_by_rebuy_cooldown(portfolio.id, stock.id, score=66, buy_threshold=50) is False

    def test_allowed_after_cooldown_expired(self, db, portfolio, stock, stop_loss_trade):
        stop_loss_trade.executed_at = datetime.now(timezone.utc) - timedelta(days=20)
        db.session.commit()
        assert blocked_by_rebuy_cooldown(portfolio.id, stock.id, score=55, buy_threshold=50) is False

    def test_allowed_without_stop_loss_history(self, db, portfolio, stock):
        assert blocked_by_rebuy_cooldown(portfolio.id, stock.id, score=55, buy_threshold=50) is False

    def test_other_sell_reasons_do_not_block(self, db, portfolio, stock):
        t = Trade(
            portfolio_id=portfolio.id, stock_id=stock.id, action='SELL',
            shares=10.0, price=100.0, price_eur=100.0, total_eur=1000.0,
            pnl_eur=500.0, reason='IBKR Fill @ 100.00 — Take-Profit erreicht',
            executed_at=datetime.now(timezone.utc) - timedelta(days=1),
        )
        db.session.add(t)
        db.session.commit()
        assert blocked_by_rebuy_cooldown(portfolio.id, stock.id, score=55, buy_threshold=50) is False
