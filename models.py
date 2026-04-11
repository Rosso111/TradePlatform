from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timezone


db = SQLAlchemy()


class JsonMixin:
    @staticmethod
    def _round(value, digits=2):
        return round(value, digits) if value is not None else None


class Stock(db.Model):
    """Handelbares Wertpapier im Universum"""
    __tablename__ = 'stocks'

    id         = db.Column(db.Integer, primary_key=True)
    symbol     = db.Column(db.String(20), unique=True, nullable=False)
    name       = db.Column(db.String(100), nullable=False)
    sector     = db.Column(db.String(50), nullable=False)
    region     = db.Column(db.String(10), nullable=False)
    currency   = db.Column(db.String(10), nullable=False, default='EUR')
    active     = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    prices      = db.relationship('Price', backref='stock', lazy='dynamic')
    positions   = db.relationship('Position', backref='stock', lazy='dynamic')
    signals     = db.relationship('Signal', backref='stock', lazy='dynamic')

    def to_dict(self):
        return {
            'id': self.id, 'symbol': self.symbol, 'name': self.name,
            'sector': self.sector, 'region': self.region, 'currency': self.currency
        }


class Price(db.Model):
    """Historische und aktuelle Kursdaten (OHLCV)"""
    __tablename__ = 'prices'

    id         = db.Column(db.Integer, primary_key=True)
    stock_id   = db.Column(db.Integer, db.ForeignKey('stocks.id'), nullable=False)
    date       = db.Column(db.Date, nullable=False)
    open       = db.Column(db.Float, nullable=False)
    high       = db.Column(db.Float, nullable=False)
    low        = db.Column(db.Float, nullable=False)
    close      = db.Column(db.Float, nullable=False)
    volume     = db.Column(db.BigInteger, default=0)
    close_eur  = db.Column(db.Float)      # In EUR umgerechnet

    __table_args__ = (db.UniqueConstraint('stock_id', 'date', name='uq_stock_date'),)

    def to_dict(self):
        return {
            'date': self.date.isoformat(),
            'open': self.open, 'high': self.high,
            'low': self.low,   'close': self.close,
            'volume': self.volume, 'close_eur': self.close_eur
        }


class ExchangeRate(db.Model):
    """Wechselkurse gegenüber EUR"""
    __tablename__ = 'exchange_rates'

    id         = db.Column(db.Integer, primary_key=True)
    pair       = db.Column(db.String(20), nullable=False)  # z.B. EURUSD
    date       = db.Column(db.Date, nullable=False)
    rate       = db.Column(db.Float, nullable=False)        # Wie viel Fremdwährung pro 1 EUR

    __table_args__ = (db.UniqueConstraint('pair', 'date', name='uq_pair_date'),)


class Account(db.Model):
    """Konto-Stand und Übersicht"""
    __tablename__ = 'account'

    id               = db.Column(db.Integer, primary_key=True)
    cash_eur         = db.Column(db.Float, nullable=False, default=10000.0)
    equity_eur       = db.Column(db.Float, nullable=False, default=10000.0)
    total_trades     = db.Column(db.Integer, default=0)
    winning_trades   = db.Column(db.Integer, default=0)
    total_commission = db.Column(db.Float, default=0.0)
    updated_at       = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc),
                                 onupdate=lambda: datetime.now(timezone.utc))

    def to_dict(self):
        win_rate = (self.winning_trades / self.total_trades * 100) if self.total_trades > 0 else 0
        return {
            'cash_eur': round(self.cash_eur, 2),
            'equity_eur': round(self.equity_eur, 2),
            'total_trades': self.total_trades,
            'win_rate': round(win_rate, 1),
            'total_commission': round(self.total_commission, 2),
        }


class Position(db.Model):
    """Offene Handelspositionen"""
    __tablename__ = 'positions'

    id              = db.Column(db.Integer, primary_key=True)
    stock_id        = db.Column(db.Integer, db.ForeignKey('stocks.id'), nullable=False)
    shares          = db.Column(db.Float, nullable=False)
    entry_price     = db.Column(db.Float, nullable=False)       # In Originalwährung
    entry_price_eur = db.Column(db.Float, nullable=False)       # In EUR
    entry_rate      = db.Column(db.Float, default=1.0)          # EUR-Kurs beim Kauf
    current_price   = db.Column(db.Float)
    current_price_eur = db.Column(db.Float)
    stop_loss       = db.Column(db.Float)                       # In Originalwährung
    take_profit     = db.Column(db.Float)                       # In Originalwährung
    trailing_stop   = db.Column(db.Float)                       # Aktueller Trailing-Stop
    highest_price   = db.Column(db.Float)                       # Höchstpreis seit Kauf (für Trailing-Stop)
    cost_eur        = db.Column(db.Float, nullable=False)       # Gesamtkosten in EUR (inkl. Provision)
    commission_eur  = db.Column(db.Float, default=0.0)
    opened_at       = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    reason          = db.Column(db.String(200))                 # Kaufbegründung

    def unrealized_pnl_eur(self):
        if self.current_price_eur is None:
            return 0.0
        return (self.current_price_eur - self.entry_price_eur) * self.shares

    def unrealized_pnl_pct(self):
        if self.entry_price_eur == 0:
            return 0.0
        return (self.current_price_eur - self.entry_price_eur) / self.entry_price_eur * 100

    def to_dict(self):
        pnl = self.unrealized_pnl_eur()
        pnl_pct = self.unrealized_pnl_pct()
        return {
            'id': self.id,
            'symbol': self.stock.symbol,
            'name': self.stock.name,
            'sector': self.stock.sector,
            'region': self.stock.region,
            'shares': round(self.shares, 4),
            'entry_price': round(self.entry_price, 4),
            'entry_price_eur': round(self.entry_price_eur, 4),
            'current_price': round(self.current_price or self.entry_price, 4),
            'current_price_eur': round(self.current_price_eur or self.entry_price_eur, 4),
            'stop_loss': round(self.stop_loss, 4) if self.stop_loss else None,
            'take_profit': round(self.take_profit, 4) if self.take_profit else None,
            'cost_eur': round(self.cost_eur, 2),
            'market_value_eur': round((self.current_price_eur or self.entry_price_eur) * self.shares, 2),
            'unrealized_pnl_eur': round(pnl, 2),
            'unrealized_pnl_pct': round(pnl_pct, 2),
            'commission_eur': round(self.commission_eur, 2),
            'opened_at': self.opened_at.isoformat(),
            'reason': self.reason,
        }


class Trade(db.Model):
    """Abgeschlossene Trades (History)"""
    __tablename__ = 'trades'

    id               = db.Column(db.Integer, primary_key=True)
    stock_id         = db.Column(db.Integer, db.ForeignKey('stocks.id'), nullable=False)
    action           = db.Column(db.String(10), nullable=False)  # 'BUY' oder 'SELL'
    shares           = db.Column(db.Float, nullable=False)
    price            = db.Column(db.Float, nullable=False)
    price_eur        = db.Column(db.Float, nullable=False)
    fx_rate          = db.Column(db.Float, default=1.0)
    commission_eur   = db.Column(db.Float, default=0.0)
    total_eur        = db.Column(db.Float, nullable=False)       # Gesamtbetrag in EUR
    pnl_eur          = db.Column(db.Float, default=0.0)          # Realisierter Gewinn/Verlust
    pnl_pct          = db.Column(db.Float, default=0.0)
    reason           = db.Column(db.String(200))
    executed_at      = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    stock = db.relationship('Stock', backref='trades')

    def to_dict(self):
        return {
            'id': self.id,
            'symbol': self.stock.symbol,
            'name': self.stock.name,
            'sector': self.stock.sector,
            'action': self.action,
            'shares': round(self.shares, 4),
            'price': round(self.price, 4),
            'price_eur': round(self.price_eur, 4),
            'fx_rate': round(self.fx_rate, 6),
            'commission_eur': round(self.commission_eur, 2),
            'total_eur': round(self.total_eur, 2),
            'pnl_eur': round(self.pnl_eur, 2),
            'pnl_pct': round(self.pnl_pct, 2),
            'reason': self.reason,
            'executed_at': self.executed_at.isoformat(),
        }


class Signal(db.Model):
    """Generierte Handelssignale"""
    __tablename__ = 'signals'

    id           = db.Column(db.Integer, primary_key=True)
    stock_id     = db.Column(db.Integer, db.ForeignKey('stocks.id'), nullable=False)
    date         = db.Column(db.Date, nullable=False)
    score        = db.Column(db.Float, nullable=False)          # 0-100
    action       = db.Column(db.String(10))                     # BUY, SELL, HOLD
    rsi          = db.Column(db.Float)
    macd         = db.Column(db.Float)
    macd_signal  = db.Column(db.Float)
    ema20        = db.Column(db.Float)
    ema50        = db.Column(db.Float)
    bb_upper     = db.Column(db.Float)
    bb_lower     = db.Column(db.Float)
    atr          = db.Column(db.Float)
    analyst_score = db.Column(db.Float, default=50.0)
    sector_score  = db.Column(db.Float, default=50.0)
    created_at   = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def to_dict(self):
        return {
            'symbol': self.stock.symbol,
            'date': self.date.isoformat(),
            'score': round(self.score, 1),
            'action': self.action,
            'rsi': round(self.rsi, 1) if self.rsi else None,
            'macd': round(self.macd, 4) if self.macd else None,
        }


class AlgoParams(db.Model):
    """Optimierte Algorithmus-Parameter pro Aktie (aus Backtesting)"""
    __tablename__ = 'algo_params'

    id              = db.Column(db.Integer, primary_key=True)
    stock_id        = db.Column(db.Integer, db.ForeignKey('stocks.id'), nullable=False, unique=True)
    rsi_period      = db.Column(db.Integer, default=14)
    rsi_oversold    = db.Column(db.Float, default=35.0)
    rsi_overbought  = db.Column(db.Float, default=65.0)
    ema_fast        = db.Column(db.Integer, default=20)
    ema_slow        = db.Column(db.Integer, default=50)
    macd_fast       = db.Column(db.Integer, default=12)
    macd_slow       = db.Column(db.Integer, default=26)
    macd_signal     = db.Column(db.Integer, default=9)
    bb_period       = db.Column(db.Integer, default=20)
    bb_std          = db.Column(db.Float, default=2.0)
    sharpe_ratio    = db.Column(db.Float, default=0.0)
    backtest_return = db.Column(db.Float, default=0.0)
    optimized_at    = db.Column(db.DateTime)

    stock = db.relationship('Stock', backref=db.backref('algo_params', uselist=False))


class EquityHistory(db.Model):
    """Tägliche Equity-Kurve für Performance-Chart"""
    __tablename__ = 'equity_history'

    id         = db.Column(db.Integer, primary_key=True)
    date       = db.Column(db.Date, nullable=False, unique=True)
    equity_eur = db.Column(db.Float, nullable=False)
    cash_eur   = db.Column(db.Float, nullable=False)
    positions_value = db.Column(db.Float, default=0.0)
    daily_pnl  = db.Column(db.Float, default=0.0)

    def to_dict(self):
        return {
            'date': self.date.isoformat(),
            'equity_eur': round(self.equity_eur, 2),
            'cash_eur': round(self.cash_eur, 2),
            'positions_value': round(self.positions_value, 2),
            'daily_pnl': round(self.daily_pnl, 2),
        }


class SimulationRun(db.Model, JsonMixin):
    __tablename__ = 'simulation_runs'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    mode = db.Column(db.String(40), nullable=False, default='historical_replay')
    status = db.Column(db.String(20), nullable=False, default='queued')
    strategy_name = db.Column(db.String(80), nullable=False, default='default_v1')
    strategy_version = db.Column(db.String(32), nullable=False, default='1.0')
    universe_name = db.Column(db.String(80), nullable=False, default='default_global_stocks')
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)
    current_date = db.Column(db.Date)
    step_interval = db.Column(db.String(10), nullable=False, default='1d')
    initial_capital_eur = db.Column(db.Float, nullable=False, default=10000.0)
    final_equity_eur = db.Column(db.Float)
    total_return_pct = db.Column(db.Float)
    benchmark_return_pct = db.Column(db.Float)
    max_drawdown_pct = db.Column(db.Float)
    sharpe_ratio = db.Column(db.Float)
    win_rate = db.Column(db.Float)
    profit_factor = db.Column(db.Float)
    total_trades = db.Column(db.Integer, default=0)
    winning_trades = db.Column(db.Integer, default=0)
    losing_trades = db.Column(db.Integer, default=0)
    notes = db.Column(db.Text)
    error_message = db.Column(db.Text)
    started_at = db.Column(db.DateTime)
    finished_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc)
    )

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'mode': self.mode,
            'status': self.status,
            'strategy_name': self.strategy_name,
            'strategy_version': self.strategy_version,
            'universe_name': self.universe_name,
            'start_date': self.start_date.isoformat() if self.start_date else None,
            'end_date': self.end_date.isoformat() if self.end_date else None,
            'current_date': self.current_date.isoformat() if self.current_date else None,
            'step_interval': self.step_interval,
            'initial_capital_eur': self._round(self.initial_capital_eur),
            'final_equity_eur': self._round(self.final_equity_eur),
            'total_return_pct': self._round(self.total_return_pct),
            'benchmark_return_pct': self._round(self.benchmark_return_pct),
            'max_drawdown_pct': self._round(self.max_drawdown_pct),
            'sharpe_ratio': self._round(self.sharpe_ratio, 4),
            'win_rate': self._round(self.win_rate),
            'profit_factor': self._round(self.profit_factor, 4),
            'total_trades': self.total_trades,
            'winning_trades': self.winning_trades,
            'losing_trades': self.losing_trades,
            'notes': self.notes,
            'error_message': self.error_message,
            'started_at': self.started_at.isoformat() if self.started_at else None,
            'finished_at': self.finished_at.isoformat() if self.finished_at else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }


class SimulationPosition(db.Model, JsonMixin):
    __tablename__ = 'simulation_positions'

    id = db.Column(db.Integer, primary_key=True)
    run_id = db.Column(db.Integer, db.ForeignKey('simulation_runs.id'), nullable=False, index=True)
    stock_id = db.Column(db.Integer, db.ForeignKey('stocks.id'), nullable=False)
    shares = db.Column(db.Float, nullable=False)
    entry_price = db.Column(db.Float, nullable=False)
    entry_price_eur = db.Column(db.Float, nullable=False)
    current_price = db.Column(db.Float)
    current_price_eur = db.Column(db.Float)
    stop_loss = db.Column(db.Float)
    take_profit = db.Column(db.Float)
    trailing_stop = db.Column(db.Float)
    highest_price = db.Column(db.Float)
    cost_eur = db.Column(db.Float, nullable=False)
    commission_eur = db.Column(db.Float, default=0.0)
    opened_at_sim_date = db.Column(db.Date, nullable=False)
    closed_at_sim_date = db.Column(db.Date)
    reason = db.Column(db.String(300))
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc)
    )

    run = db.relationship('SimulationRun', backref=db.backref('simulation_positions', lazy='dynamic', cascade='all, delete-orphan'))
    stock = db.relationship('Stock')

    def to_dict(self):
        return {
            'id': self.id,
            'run_id': self.run_id,
            'symbol': self.stock.symbol,
            'name': self.stock.name,
            'shares': self._round(self.shares, 4),
            'entry_price': self._round(self.entry_price, 4),
            'entry_price_eur': self._round(self.entry_price_eur, 4),
            'current_price': self._round(self.current_price, 4),
            'current_price_eur': self._round(self.current_price_eur, 4),
            'stop_loss': self._round(self.stop_loss, 4),
            'take_profit': self._round(self.take_profit, 4),
            'trailing_stop': self._round(self.trailing_stop, 4),
            'highest_price': self._round(self.highest_price, 4),
            'cost_eur': self._round(self.cost_eur),
            'commission_eur': self._round(self.commission_eur),
            'opened_at_sim_date': self.opened_at_sim_date.isoformat() if self.opened_at_sim_date else None,
            'closed_at_sim_date': self.closed_at_sim_date.isoformat() if self.closed_at_sim_date else None,
            'reason': self.reason,
        }


class DecisionLog(db.Model, JsonMixin):
    __tablename__ = 'decision_logs'

    id = db.Column(db.Integer, primary_key=True)
    run_id = db.Column(db.Integer, db.ForeignKey('simulation_runs.id'), nullable=False, index=True)
    stock_id = db.Column(db.Integer, db.ForeignKey('stocks.id'), nullable=False)
    sim_date = db.Column(db.Date, nullable=False, index=True)
    action = db.Column(db.String(10), nullable=False)
    final_score = db.Column(db.Float, nullable=False, default=0.0)
    technical_score = db.Column(db.Float)
    analyst_score = db.Column(db.Float)
    news_score = db.Column(db.Float)
    sector_score = db.Column(db.Float)
    risk_score = db.Column(db.Float)
    current_price = db.Column(db.Float)
    current_price_eur = db.Column(db.Float)
    atr = db.Column(db.Float)
    rsi = db.Column(db.Float)
    macd = db.Column(db.Float)
    ema_fast = db.Column(db.Float)
    ema_slow = db.Column(db.Float)
    reason_summary = db.Column(db.String(500))
    reason_json = db.Column(db.JSON)
    risk_json = db.Column(db.JSON)
    data_snapshot_json = db.Column(db.JSON)
    executed = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    run = db.relationship('SimulationRun', backref=db.backref('decision_logs', lazy='dynamic', cascade='all, delete-orphan'))
    stock = db.relationship('Stock')

    def to_dict(self):
        return {
            'id': self.id,
            'run_id': self.run_id,
            'symbol': self.stock.symbol,
            'name': self.stock.name,
            'sim_date': self.sim_date.isoformat() if self.sim_date else None,
            'action': self.action,
            'final_score': self._round(self.final_score, 2),
            'technical_score': self._round(self.technical_score, 2),
            'analyst_score': self._round(self.analyst_score, 2),
            'news_score': self._round(self.news_score, 2),
            'sector_score': self._round(self.sector_score, 2),
            'risk_score': self._round(self.risk_score, 2),
            'current_price': self._round(self.current_price, 4),
            'current_price_eur': self._round(self.current_price_eur, 4),
            'atr': self._round(self.atr, 4),
            'rsi': self._round(self.rsi, 2),
            'macd': self._round(self.macd, 4),
            'ema_fast': self._round(self.ema_fast, 4),
            'ema_slow': self._round(self.ema_slow, 4),
            'reason_summary': self.reason_summary,
            'reason_json': self.reason_json or {},
            'risk_json': self.risk_json or {},
            'data_snapshot_json': self.data_snapshot_json or {},
            'executed': self.executed,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class SimulationTrade(db.Model, JsonMixin):
    __tablename__ = 'simulation_trades'

    id = db.Column(db.Integer, primary_key=True)
    run_id = db.Column(db.Integer, db.ForeignKey('simulation_runs.id'), nullable=False, index=True)
    stock_id = db.Column(db.Integer, db.ForeignKey('stocks.id'), nullable=False)
    action = db.Column(db.String(10), nullable=False)
    sim_date = db.Column(db.Date, nullable=False, index=True)
    shares = db.Column(db.Float, nullable=False)
    price = db.Column(db.Float, nullable=False)
    price_eur = db.Column(db.Float, nullable=False)
    fx_rate = db.Column(db.Float, default=1.0)
    commission_eur = db.Column(db.Float, default=0.0)
    spread_eur = db.Column(db.Float, default=0.0)
    total_eur = db.Column(db.Float, nullable=False)
    pnl_eur = db.Column(db.Float, default=0.0)
    pnl_pct = db.Column(db.Float, default=0.0)
    reason = db.Column(db.String(300))
    decision_log_id = db.Column(db.Integer, db.ForeignKey('decision_logs.id'))
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    run = db.relationship('SimulationRun', backref=db.backref('simulation_trades', lazy='dynamic', cascade='all, delete-orphan'))
    stock = db.relationship('Stock')
    decision_log = db.relationship('DecisionLog')

    def to_dict(self):
        return {
            'id': self.id,
            'run_id': self.run_id,
            'symbol': self.stock.symbol,
            'name': self.stock.name,
            'sector': self.stock.sector,
            'action': self.action,
            'sim_date': self.sim_date.isoformat() if self.sim_date else None,
            'shares': self._round(self.shares, 4),
            'price': self._round(self.price, 4),
            'price_eur': self._round(self.price_eur, 4),
            'fx_rate': self._round(self.fx_rate, 6),
            'commission_eur': self._round(self.commission_eur),
            'spread_eur': self._round(self.spread_eur),
            'total_eur': self._round(self.total_eur),
            'pnl_eur': self._round(self.pnl_eur),
            'pnl_pct': self._round(self.pnl_pct, 2),
            'reason': self.reason,
            'decision_log_id': self.decision_log_id,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class SimulationDailySnapshot(db.Model, JsonMixin):
    __tablename__ = 'simulation_daily_snapshots'

    id = db.Column(db.Integer, primary_key=True)
    run_id = db.Column(db.Integer, db.ForeignKey('simulation_runs.id'), nullable=False, index=True)
    sim_date = db.Column(db.Date, nullable=False, index=True)
    cash_eur = db.Column(db.Float, nullable=False)
    positions_value_eur = db.Column(db.Float, default=0.0)
    equity_eur = db.Column(db.Float, nullable=False)
    daily_pnl_eur = db.Column(db.Float, default=0.0)
    drawdown_pct = db.Column(db.Float, default=0.0)
    open_positions = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (db.UniqueConstraint('run_id', 'sim_date', name='uq_run_sim_date'),)

    run = db.relationship('SimulationRun', backref=db.backref('daily_snapshots', lazy='dynamic', cascade='all, delete-orphan'))

    def to_dict(self):
        return {
            'id': self.id,
            'run_id': self.run_id,
            'sim_date': self.sim_date.isoformat() if self.sim_date else None,
            'cash_eur': self._round(self.cash_eur),
            'positions_value_eur': self._round(self.positions_value_eur),
            'equity_eur': self._round(self.equity_eur),
            'daily_pnl_eur': self._round(self.daily_pnl_eur),
            'drawdown_pct': self._round(self.drawdown_pct, 2),
            'open_positions': self.open_positions,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }
