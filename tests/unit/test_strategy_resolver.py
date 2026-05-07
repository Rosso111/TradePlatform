"""Unit-Tests für services/strategy_resolver.py — keine DB nötig."""
import types
import pytest

import config
from services.strategy_resolver import resolve, PARAM_DEFAULTS


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_portfolio(strategy=None, strategy_id=None):
    p = types.SimpleNamespace(strategy=strategy, strategy_id=strategy_id)
    return p


def _make_strategy(params=None, rules=None):
    s = types.SimpleNamespace(
        params=params or {},
        rules=types.SimpleNamespace(all=lambda: rules or []),
    )
    return s


def _make_rule(level, key, overrides):
    return types.SimpleNamespace(level=level, key=key, overrides=overrides)


def _make_stock(region='US', sector='Technology', symbol='AAPL'):
    return types.SimpleNamespace(region=region, sector=sector, symbol=symbol)


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_defaults_when_no_strategy():
    portfolio = _make_portfolio()
    params = resolve(portfolio)
    assert params == PARAM_DEFAULTS


def test_strategy_params_override_defaults():
    strategy = _make_strategy(params={'buy_threshold': 70, 'max_positions': 5})
    portfolio = _make_portfolio(strategy=strategy)
    params = resolve(portfolio)
    assert params['buy_threshold'] == 70
    assert params['max_positions'] == 5
    assert params['sell_threshold'] == config.SIGNAL_THRESHOLD_SELL  # unchanged


def test_unknown_strategy_param_ignored():
    strategy = _make_strategy(params={'nonexistent_param': 999})
    portfolio = _make_portfolio(strategy=strategy)
    params = resolve(portfolio)
    assert 'nonexistent_param' not in params


def test_market_rule_applied():
    rule = _make_rule('market', 'US', {'buy_threshold': 72})
    strategy = _make_strategy(rules=[rule])
    portfolio = _make_portfolio(strategy=strategy)
    stock = _make_stock(region='US')
    params = resolve(portfolio, stock)
    assert params['buy_threshold'] == 72


def test_sector_rule_overrides_market_rule():
    rules = [
        _make_rule('market', 'US',         {'buy_threshold': 72}),
        _make_rule('sector', 'Technology', {'buy_threshold': 75}),
    ]
    strategy = _make_strategy(rules=rules)
    portfolio = _make_portfolio(strategy=strategy)
    stock = _make_stock(region='US', sector='Technology')
    params = resolve(portfolio, stock)
    assert params['buy_threshold'] == 75  # sector wins over market


def test_stock_rule_overrides_all():
    rules = [
        _make_rule('market', 'US',         {'buy_threshold': 72}),
        _make_rule('sector', 'Technology', {'buy_threshold': 75}),
        _make_rule('stock',  'AAPL',       {'buy_threshold': 80}),
    ]
    strategy = _make_strategy(rules=rules)
    portfolio = _make_portfolio(strategy=strategy)
    stock = _make_stock(region='US', sector='Technology', symbol='AAPL')
    params = resolve(portfolio, stock)
    assert params['buy_threshold'] == 80  # stock-rule wins


def test_rule_for_different_market_not_applied():
    rule = _make_rule('market', 'DE', {'buy_threshold': 60})
    strategy = _make_strategy(rules=[rule])
    portfolio = _make_portfolio(strategy=strategy)
    stock = _make_stock(region='US')
    params = resolve(portfolio, stock)
    assert params['buy_threshold'] == config.SIGNAL_THRESHOLD_BUY  # DE rule not applied


def test_missing_stock_skips_rules():
    rules = [_make_rule('stock', 'AAPL', {'buy_threshold': 80})]
    strategy = _make_strategy(rules=rules)
    portfolio = _make_portfolio(strategy=strategy)
    params = resolve(portfolio, stock=None)  # no stock
    assert params['buy_threshold'] == config.SIGNAL_THRESHOLD_BUY


def test_strategy_base_then_market_then_sector():
    rules = [_make_rule('market', 'US', {'risk_per_trade': 0.03})]
    strategy = _make_strategy(
        params={'risk_per_trade': 0.01, 'max_positions': 8},
        rules=rules,
    )
    portfolio = _make_portfolio(strategy=strategy)
    stock = _make_stock(region='US', sector='Financials')
    params = resolve(portfolio, stock)
    assert params['risk_per_trade'] == 0.03   # market rule overrides strategy base
    assert params['max_positions']  == 8      # strategy base, no rule overrides it


def test_dict_stock_interface():
    rules = [_make_rule('stock', 'TSLA', {'trailing_stop_pct': 0.05})]
    strategy = _make_strategy(rules=rules)
    portfolio = _make_portfolio(strategy=strategy)
    stock_dict = {'region': 'US', 'sector': 'Automotive', 'symbol': 'TSLA'}
    params = resolve(portfolio, stock_dict)
    assert params['trailing_stop_pct'] == 0.05
