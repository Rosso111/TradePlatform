"""
StrategyResolver — löst Handelsparameter für ein Portfolio + optionale Aktie auf.

Hierarchie (feinste Regel gewinnt):
  Global-Defaults → Strategy.params → Markt-Regel → Sektor-Regel → Aktien-Regel

Implements: S-06, S-07, S-08, S-09, S-10
"""
import config
from models import Portfolio

PARAM_DEFAULTS: dict = {
    'buy_threshold':           config.SIGNAL_THRESHOLD_BUY,
    'sell_threshold':          config.SIGNAL_THRESHOLD_SELL,
    'risk_per_trade':          config.RISK_PER_TRADE,
    'max_positions':           config.MAX_POSITIONS,
    'max_positions_per_sector': config.MAX_POSITIONS_PER_SECTOR,
    'max_position_size':       config.MAX_POSITION_SIZE,
    'min_position_size':       config.MIN_POSITION_SIZE,
    'default_stop_loss_pct':   config.DEFAULT_STOP_LOSS_PCT,
    'atr_stop_multiplier':     config.ATR_STOP_MULTIPLIER,
    'trailing_stop_pct':       config.TRAILING_STOP_PCT,
    'commission_rate':         config.COMMISSION_RATE,
    'min_commission':          config.MIN_COMMISSION,
    'spread_rate':             config.SPREAD_RATE,
    'default_take_profit_pct': config.DEFAULT_TAKE_PROFIT_PCT,
}


def resolve(portfolio: Portfolio, stock=None) -> dict:
    """
    Gibt die effektiven Handelsparameter für portfolio + optionale Aktie zurück.
    stock kann ein Stock-ORM-Objekt oder ein dict mit 'region'/'sector'/'symbol' sein.
    """
    params = dict(PARAM_DEFAULTS)

    strategy = getattr(portfolio, 'strategy', None)
    if strategy is None and portfolio.strategy_id:
        from models import Strategy
        strategy = Strategy.query.get(portfolio.strategy_id)

    if strategy and strategy.params:
        for k, v in strategy.params.items():
            if k in params:
                params[k] = v

    if strategy and stock:
        rules = list(strategy.rules.all())
        if hasattr(stock, 'region'):
            market = stock.region
            sector = stock.sector
            symbol = stock.symbol
        else:
            market = stock.get('region')
            sector = stock.get('sector')
            symbol = stock.get('symbol')

        for level, key in [('market', market), ('sector', sector), ('stock', symbol)]:
            if not key:
                continue
            for rule in rules:
                if rule.level == level and rule.key == key:
                    for k, v in (rule.overrides or {}).items():
                        if k in params:
                            params[k] = v
                    break

    return params
