"""
Flask Application Factory
Initialisiert App, Datenbank, WebSocket und den autonomen Scheduler.
"""
import logging
import logging.handlers
import os
from datetime import datetime, timezone

from dotenv import load_dotenv
from flask import Flask, render_template
from flask_socketio import SocketIO
from flask_cors import CORS
from flask_migrate import Migrate
from flask_login import LoginManager
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

import config
from models import db, Account

_LOG_FORMAT = '%(asctime)s [%(levelname)s] %(name)s: %(message)s'
logging.basicConfig(level=logging.INFO, format=_LOG_FORMAT)

_log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logs')
os.makedirs(_log_dir, exist_ok=True)
_file_handler = logging.handlers.TimedRotatingFileHandler(
    filename=os.path.join(_log_dir, 'tradeplatform.log'),
    when='midnight',
    interval=1,
    backupCount=30,
    encoding='utf-8',
)
_file_handler.setFormatter(logging.Formatter(_LOG_FORMAT))
_file_handler.setLevel(logging.INFO)
logging.getLogger().addHandler(_file_handler)

log = logging.getLogger(__name__)

socketio = SocketIO()
scheduler = BackgroundScheduler(timezone='Europe/Vienna')
migrate = Migrate()
login_manager = LoginManager()
limiter = Limiter(key_func=get_remote_address)


def create_app(test_config: dict | None = None):
    base_dir = os.path.dirname(os.path.abspath(__file__))
    if not test_config:
        load_dotenv(os.path.join(base_dir, '.env'))
        load_dotenv(os.path.join(base_dir, '.env.local'), override=True)
    app = Flask(__name__)
    app.config['SECRET_KEY'] = config.SECRET_KEY
    app.config['SQLALCHEMY_DATABASE_URI'] = config.SQLALCHEMY_DATABASE_URI
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0
    app.config['SESSION_COOKIE_HTTPONLY'] = True
    app.config['SESSION_COOKIE_SAMESITE'] = 'Strict'
    app.config['REMEMBER_COOKIE_HTTPONLY'] = True
    app.config['REMEMBER_COOKIE_SAMESITE'] = 'Strict'
    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
        'connect_args': {
            'connect_timeout': 10,
        },
        'pool_pre_ping': True,
    }

    if test_config:
        app.config.update(test_config)

    # Extensions
    db.init_app(app)
    migrate.init_app(app, db)
    CORS(app)
    # threading statt eventlet: eventlet ist deprecated und verträgt sich
    # schlecht mit APScheduler-Threads und ib_insync (asyncio). WebSocket-
    # Transport läuft über simple-websocket weiter.
    socketio.init_app(app, cors_allowed_origins='*', async_mode='threading')
    login_manager.init_app(app)
    limiter.init_app(app)
    login_manager.login_view = 'auth.login'

    @login_manager.unauthorized_handler
    def unauthorized():
        from flask import jsonify as _jsonify
        return _jsonify({'error': 'Anmeldung erforderlich'}), 401

    @login_manager.user_loader
    def load_user(user_id):
        from models import User
        return User.query.get(int(user_id))

    # Blueprints
    from routes.api import api
    from routes.auth import auth_bp
    from routes.users import users_bp
    from routes.portfolios import portfolios_bp
    from routes.proposals import proposals_bp
    from routes.trading import trading_bp
    from routes.simulations import simulations_bp
    from routes.scenarios import scenarios_bp
    from routes.strategies import strategies_bp
    from routes.ibkr import ibkr_bp
    app.register_blueprint(api)
    app.register_blueprint(auth_bp)
    app.register_blueprint(users_bp)
    app.register_blueprint(portfolios_bp)
    app.register_blueprint(proposals_bp)
    app.register_blueprint(trading_bp)
    app.register_blueprint(simulations_bp)
    app.register_blueprint(scenarios_bp)
    app.register_blueprint(strategies_bp)
    app.register_blueprint(ibkr_bp)

    # Hauptseite
    @app.route('/')
    def index():
        return render_template('index.html')

    # Datenbank & Startdaten initialisieren
    with app.app_context():
        db.create_all()
        if not test_config:
            try:
                _init_account()
                _init_admin_user()
                _init_performance_indexes(app)
                _cleanup_stuck_simulation_runs()
                log.info("Datenbank initialisiert.")
            except Exception as e:
                db.session.rollback()
                log.warning("Startup-Initialisierung übersprungen (Schema-Migration ausstehend?): %s", e)

    if not test_config:
        _setup_scheduler(app)
        _initial_data_load(app)

    return app


def _init_performance_indexes(app):
    """Legt wichtige Performance-Indizes fuer Replay/API-Last an."""

    index_statements = [
        'CREATE INDEX IF NOT EXISTS idx_prices_stock_date_desc ON prices (stock_id, date DESC)',
        'CREATE INDEX IF NOT EXISTS idx_decision_logs_run_date_id ON decision_logs (run_id, sim_date DESC, id DESC)',
        'CREATE INDEX IF NOT EXISTS idx_decision_logs_run_executed ON decision_logs (run_id, executed)',
        'CREATE INDEX IF NOT EXISTS idx_simulation_trades_run_date_id ON simulation_trades (run_id, sim_date DESC, id DESC)',
        'CREATE INDEX IF NOT EXISTS idx_simulation_trades_decision_log_id ON simulation_trades (decision_log_id)',
        'CREATE INDEX IF NOT EXISTS idx_simulation_positions_run_stock ON simulation_positions (run_id, stock_id)',
        'CREATE INDEX IF NOT EXISTS idx_simulation_daily_snapshots_run_date_desc ON simulation_daily_snapshots (run_id, sim_date DESC)',
        # Live-Trading Indizes
        'CREATE INDEX IF NOT EXISTS idx_positions_portfolio_stock ON positions (portfolio_id, stock_id)',
        'CREATE INDEX IF NOT EXISTS idx_signals_portfolio_date_desc ON signals (portfolio_id, date DESC)',
        'CREATE INDEX IF NOT EXISTS idx_signals_stock_date_desc ON signals (stock_id, date DESC)',
        'CREATE INDEX IF NOT EXISTS idx_trades_portfolio_date ON trades (portfolio_id, executed_at DESC)',
        'CREATE INDEX IF NOT EXISTS idx_equity_history_portfolio_date ON equity_history (portfolio_id, date DESC)',
    ]

    try:
        for stmt in index_statements:
            db.session.execute(text(stmt))
        db.session.commit()
        log.info('Performance-Indizes geprüft/angelegt.')
    except SQLAlchemyError as e:
        db.session.rollback()
        log.warning('Performance-Indizes konnten nicht angelegt werden: %s', e)


def _cleanup_stuck_simulation_runs():
    """Markiert Runs als failed, deren Background-Thread durch einen Neustart abgebrochen wurde."""
    from models import SimulationRun
    stuck = SimulationRun.query.filter(
        SimulationRun.status.in_(['running', 'cancel_requested'])
    ).all()
    if not stuck:
        return
    for run in stuck:
        run.status = 'failed'
        run.error_message = 'Run unterbrochen (App-Neustart)'
        run.finished_at = datetime.now(timezone.utc)
    db.session.commit()
    log.warning('Startup-Cleanup: %d unterbrochene Runs auf failed gesetzt.', len(stuck))


def _init_account(portfolio=None):
    """Implements: P-05, DB-03"""
    if portfolio is None:
        # Legacy-Guard: bestehende Account-Row ohne portfolio_id (vor Migration)
        if not Account.query.filter_by(portfolio_id=None).first():
            return
        return
    if not Account.query.filter_by(portfolio_id=portfolio.id).first():
        account = Account(
            portfolio_id=portfolio.id,
            cash_eur=portfolio.starting_capital,
            equity_eur=portfolio.starting_capital,
        )
        db.session.add(account)
        db.session.commit()
        log.info("Account für Portfolio '%s' angelegt: %.2f EUR", portfolio.name, portfolio.starting_capital)


def _init_admin_user():
    """Legt einen Admin-User an, falls die User-Tabelle leer ist. Implements: DB-03, U-06"""
    import secrets
    from models import User, Portfolio
    if User.query.first():
        return
    default_pw = os.environ.get('ADMIN_DEFAULT_PASSWORD') or secrets.token_urlsafe(16)
    admin = User(username='admin', email=None, role='admin')
    admin.set_password(default_pw)
    db.session.add(admin)
    db.session.flush()  # admin.id verfügbar machen

    portfolio = Portfolio(
        user_id=admin.id,
        name='Default',
        type='sim',
        mode='auto',
        status='active',
        currency='EUR',
        starting_capital=config.STARTING_CAPITAL,
    )
    db.session.add(portfolio)
    db.session.flush()  # portfolio.id verfügbar machen

    db.session.commit()
    _init_account(portfolio)

    if not os.environ.get('ADMIN_DEFAULT_PASSWORD'):
        log.warning(
            "Admin-User angelegt mit zufälligem Passwort. "
            "Passwort via ADMIN_DEFAULT_PASSWORD env-Variable setzen oder sofort ändern: "
            "PUT /api/users/1/password"
        )
    else:
        log.warning("Admin-User angelegt — Passwort sofort ändern! (PUT /api/users/1/password)")


def _ensure_ibkr_gateway():
    """Startet ibgateway via systemd falls nicht verbunden. Wartet nicht auf Ready."""
    import subprocess, time as _time
    from services.ibkr_connector import IBKRConnectionPool
    conn = IBKRConnectionPool.get(config.IBKR_HOST, config.IBKR_PAPER_PORT, config.IBKR_CLIENT_ID)
    if conn.is_connected():
        return
    if _time.time() < conn._backoff_until:
        return  # Circuit-Breaker aktiv — systemd startet den Gateway bereits
    try:
        result = subprocess.run(
            ['systemctl', 'start', 'ibgateway'],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            log.info('ibgateway.service gestartet (auto-recovery).')
        else:
            subprocess.Popen(
                ['/home/martin/ibgateway/run_gateway.sh'],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            log.info('ibgateway direkt gestartet (run_gateway.sh).')
    except Exception as e:
        log.warning('Gateway-Auto-Start fehlgeschlagen: %s', e)


def _setup_scheduler(app):
    """Richtet den autonomen Handelstakt ein."""

    def trading_job():
        from datetime import date
        if date.today().isoweekday() >= 6:  # 6=Samstag, 7=Sonntag
            return

        all_actions = []

        # Sim-Portfolios — immer aktiv
        try:
            from services.trading_engine import run_trading_cycle
            actions = run_trading_cycle(app)
            all_actions.extend(actions)
        except Exception as e:
            log.error("Sim-Handelszyklus fehlgeschlagen: %s", e)

        # IBKR-Portfolios — nur wenn Live-Trading aktiviert
        if config.LIVE_TRADING:
            _ensure_ibkr_gateway()
            try:
                from services.live_runner import run_live_trading_cycle
                actions = run_live_trading_cycle(app)
                all_actions.extend(actions)
            except Exception as e:
                log.error("IBKR-Handelszyklus fehlgeschlagen: %s", e)

        if all_actions:
            socketio.emit('trading_actions', {'actions': all_actions})
        socketio.emit('portfolio_update', _get_portfolio_snapshot(app))

    def equity_broadcast():
        """Pusht Echtzeit-Portfolio-Daten ans Frontend."""
        try:
            socketio.emit('portfolio_update', _get_portfolio_snapshot(app))
        except Exception as e:
            log.error(f"Equity-Broadcast: {e}")

    # Haupthandels-Zyklus alle 15 Minuten
    scheduler.add_job(
        trading_job,
        trigger=IntervalTrigger(minutes=config.TRADING_INTERVAL_MINUTES),
        id='trading_cycle',
        replace_existing=True,
    )

    # Portfolio-Update jede Minute ans Frontend pushen
    scheduler.add_job(
        equity_broadcast,
        trigger=IntervalTrigger(minutes=1),
        id='equity_broadcast',
        replace_existing=True,
    )

    # Implements: PR-03 — Tagesvorschläge + Signal-Notification um 8:00 Uhr MEZ
    def proposal_generate_job():
        from datetime import date
        if date.today().isoweekday() >= 6:  # 6=Samstag, 7=Sonntag
            log.info("Proposal-Job: Wochenende — übersprungen.")
            return

        from services.proposal_generator import generate_daily_proposals
        try:
            count = generate_daily_proposals(app)
            log.info("Proposal-Generierung abgeschlossen: %d neue Proposals.", count)
        except Exception as e:
            log.error("Proposal-Generierung fehlgeschlagen: %s", e)

        try:
            from services.algorithm import generate_signals
            from services.telegram_notifier import notify_signals
            signals = generate_signals(app)
            notify_signals(signals)
            log.info("Tages-Signale via Telegram verschickt.")
        except Exception as e:
            log.error("Signal-Notification fehlgeschlagen: %s", e)

    scheduler.add_job(
        proposal_generate_job,
        trigger=CronTrigger(hour=8, minute=0, timezone='Europe/Vienna'),
        id='proposal_generate',
        replace_existing=True,
    )

    # Implements: PR-09 — Veraltete Proposals um 22:00 Uhr auf 'expired' setzen
    def proposal_expire_job():
        from services.proposal_generator import expire_stale_proposals
        try:
            count = expire_stale_proposals(app)
            log.info("Proposal-Expiry abgeschlossen: %d Proposals abgelaufen.", count)
        except Exception as e:
            log.error("Proposal-Expiry fehlgeschlagen: %s", e)

    scheduler.add_job(
        proposal_expire_job,
        trigger=CronTrigger(hour=22, minute=0, timezone='Europe/Vienna'),
        id='proposal_expire',
        replace_existing=True,
    )

    # Tagesbericht Mo–Fr um 22:15 Uhr (nach Proposal-Expiry)
    def report_daily_job():
        from services.report_generator import generate_daily_report
        from services.telegram_notifier import send_message
        try:
            filepath, tg = generate_daily_report(app)
            send_message(tg)
            log.info("Tagesbericht erstellt: %s", filepath)
        except Exception as e:
            log.error("Tagesbericht fehlgeschlagen: %s", e)

    scheduler.add_job(
        report_daily_job,
        trigger=CronTrigger(day_of_week='mon-fri', hour=22, minute=15, timezone='Europe/Vienna'),
        id='report_daily',
        replace_existing=True,
    )

    # Wochenbericht jeden Samstag um 09:00 Uhr
    def report_weekly_job():
        from services.report_generator import generate_weekly_report
        from services.telegram_notifier import send_message
        try:
            filepath, tg = generate_weekly_report(app)
            send_message(tg)
            log.info("Wochenbericht erstellt: %s", filepath)
        except Exception as e:
            log.error("Wochenbericht fehlgeschlagen: %s", e)

    scheduler.add_job(
        report_weekly_job,
        trigger=CronTrigger(day_of_week='sat', hour=9, minute=0, timezone='Europe/Vienna'),
        id='report_weekly',
        replace_existing=True,
    )

    # Monatsbericht am 1. jeden Monats um 09:00 Uhr (deckt Vormonat ab)
    def report_monthly_job():
        from datetime import date, timedelta
        from services.report_generator import generate_monthly_report
        from services.telegram_notifier import send_message
        last_month = date.today().replace(day=1) - timedelta(days=1)
        try:
            filepath, tg = generate_monthly_report(app, last_month)
            send_message(tg)
            log.info("Monatsbericht erstellt: %s", filepath)
        except Exception as e:
            log.error("Monatsbericht fehlgeschlagen: %s", e)

    scheduler.add_job(
        report_monthly_job,
        trigger=CronTrigger(day=1, hour=9, minute=15, timezone='Europe/Vienna'),
        id='report_monthly',
        replace_existing=True,
    )

    # Quartalsbericht am 1. April / Juli / Oktober / Jänner um 09:30 Uhr
    def report_quarterly_job():
        from datetime import date, timedelta
        from services.report_generator import generate_quarterly_report
        from services.telegram_notifier import send_message
        last_quarter_day = date.today().replace(day=1) - timedelta(days=1)
        try:
            filepath, tg = generate_quarterly_report(app, last_quarter_day)
            send_message(tg)
            log.info("Quartalsbericht erstellt: %s", filepath)
        except Exception as e:
            log.error("Quartalsbericht fehlgeschlagen: %s", e)

    scheduler.add_job(
        report_quarterly_job,
        trigger=CronTrigger(month='1,4,7,10', day=1, hour=9, minute=30, timezone='Europe/Vienna'),
        id='report_quarterly',
        replace_existing=True,
    )

    # Jahresbericht am 1. Jänner um 10:00 Uhr (deckt Vorjahr ab)
    def report_yearly_job():
        from services.report_generator import generate_yearly_report
        from services.telegram_notifier import send_message
        try:
            filepath, tg = generate_yearly_report(app)
            send_message(tg)
            log.info("Jahresbericht erstellt: %s", filepath)
        except Exception as e:
            log.error("Jahresbericht fehlgeschlagen: %s", e)

    scheduler.add_job(
        report_yearly_job,
        trigger=CronTrigger(month=1, day=1, hour=10, minute=0, timezone='Europe/Vienna'),
        id='report_yearly',
        replace_existing=True,
    )

    scheduler.start()
    log.info(f"Scheduler gestartet. Handelszyklus alle {config.TRADING_INTERVAL_MINUTES} Minuten.")

    try:
        from services.telegram_notifier import start_polling
        start_polling(app)
    except Exception as e:
        log.warning('Telegram polling konnte nicht gestartet werden: %s', e)


def _initial_data_load(app):
    """Lädt Kursdaten beim ersten Start (Hintergrundthread)."""
    import threading

    def load():
        with app.app_context():
            from models import Stock, Price
            stock_count = Stock.query.count()
            price_count = Price.query.count()

            if price_count < 1000:
                log.info("Initialer Datenladevorgang gestartet...")
                total = len(config.STOCK_UNIVERSE)
                socketio.emit('status', {
                    'message': f'Lade Kursdaten für {total} Aktien (Erststart, bitte warten)...'
                })
                try:
                    from services.data_fetcher import (
                        fetch_exchange_rates, fetch_multiple_prices,
                        store_prices_to_db
                    )
                    from models import db, Stock, Price, ExchangeRate
                    from datetime import date, timedelta, datetime

                    # 1. Wechselkurse
                    socketio.emit('status', {'message': 'Lade Wechselkurse...'})
                    rates = fetch_exchange_rates()
                    today = date.today()
                    with app.app_context():
                        for currency, rate in rates.items():
                            if currency == 'EUR':
                                continue
                            pair = f'EUR{currency}'
                            if not ExchangeRate.query.filter_by(pair=pair, date=today).first():
                                db.session.add(ExchangeRate(pair=pair, date=today, rate=rate))
                        db.session.commit()

                    # 2. Aktien anlegen
                    with app.app_context():
                        for stock_info in config.STOCK_UNIVERSE:
                            if not Stock.query.filter_by(symbol=stock_info['symbol']).first():
                                db.session.add(Stock(**{
                                    k: v for k, v in stock_info.items()
                                }))
                        db.session.commit()

                    # 3. Kursdaten in Batches mit Fortschrittsanzeige
                    symbols = [s['symbol'] for s in config.STOCK_UNIVERSE]
                    batch_size = 10
                    for i in range(0, len(symbols), batch_size):
                        batch = symbols[i:i + batch_size]
                        loaded = min(i + batch_size, len(symbols))
                        socketio.emit('status', {
                            'message': f'Lade Kursdaten... {loaded}/{total} Aktien'
                        })
                        try:
                            from services.data_fetcher import fetch_multiple_prices
                            price_data = fetch_multiple_prices(batch, days=400)
                            end = datetime.now()
                            start_dt = end - timedelta(days=400)

                            with app.app_context():
                                for symbol, df in price_data.items():
                                    stock = Stock.query.filter_by(symbol=symbol).first()
                                    if not stock:
                                        continue
                                    currency = next(
                                        (s['currency'] for s in config.STOCK_UNIVERSE
                                         if s['symbol'] == symbol), 'EUR'
                                    )
                                    fx_rate = rates.get(currency, 1.0)
                                    existing_dates = {
                                        p.date for p in Price.query.filter_by(stock_id=stock.id)
                                    }
                                    new_prices = []
                                    for idx_date, row in df.iterrows():
                                        if idx_date not in existing_dates:
                                            close_val = float(row['Close'])
                                            close_eur = close_val / fx_rate if fx_rate > 0 else close_val
                                            new_prices.append(Price(
                                                stock_id=stock.id, date=idx_date,
                                                open=float(row['Open']), high=float(row['High']),
                                                low=float(row['Low']), close=close_val,
                                                volume=int(row.get('Volume', 0) or 0),
                                                close_eur=close_eur,
                                            ))
                                    if new_prices:
                                        db.session.bulk_save_objects(new_prices)
                                db.session.commit()
                        except Exception as e:
                            log.warning(f"Batch {i}-{i+batch_size}: {e}")

                    # 4. Optimierung
                    socketio.emit('status', {'message': 'Optimiere Algorithmus-Parameter...'})
                    from services.algorithm import run_optimization_for_all
                    run_optimization_for_all(app)

                    log.info("System bereit.")
                    socketio.emit('status', {'message': 'System bereit. Autonomer Handel aktiv.'})
                    socketio.emit('portfolio_update', _get_portfolio_snapshot(app))
                except Exception as e:
                    log.error(f"Initialer Ladevorgang: {e}")
                    socketio.emit('status', {'message': 'System bereit (mit Fehlern).'})
            else:
                log.info(f"Kursdaten vorhanden ({price_count} Einträge). Starte direkt.")
                socketio.emit('status', {'message': 'System bereit. Autonomer Handel aktiv.'})
                try:
                    from services.data_fetcher import update_prices_incremental
                    update_prices_incremental(app, config.STOCK_UNIVERSE)
                    socketio.emit('portfolio_update', _get_portfolio_snapshot(app))
                except Exception as e:
                    log.warning(f"Inkrementelles Update: {e}")

    thread = threading.Thread(target=load, daemon=True)
    thread.start()


def _get_portfolio_snapshot(app, portfolio_id=None) -> dict:
    """Implements: G-02, P-05"""
    from models import Account, Position, Portfolio
    with app.app_context():
        account = None
        if portfolio_id:
            account = Account.query.filter_by(portfolio_id=portfolio_id).first()
        if not account:
            # Fallback: Admin-Portfolio (Scheduler-Kontext ohne User-Session)
            admin_portfolio = Portfolio.query.join(
                __import__('models').User,
                Portfolio.user_id == __import__('models').User.id
            ).filter(
                __import__('models').User.role == 'admin'
            ).order_by(Portfolio.id).first()
            if admin_portfolio:
                account = Account.query.filter_by(portfolio_id=admin_portfolio.id).first()
                portfolio_id = admin_portfolio.id if admin_portfolio else None
        if not account:
            return {}
        positions = []
        pos_value = 0.0
        pos_query = Position.query.filter_by(portfolio_id=account.portfolio_id) if account.portfolio_id else Position.query
        for p in pos_query.all():
            pnl = p.unrealized_pnl_eur()
            pos_value += (p.current_price_eur or p.entry_price_eur) * p.shares
            positions.append({
                'symbol': p.stock.symbol,
                'shares': round(p.shares, 4),
                'entry_price_eur': round(p.entry_price_eur, 4),
                'current_price_eur': round(p.current_price_eur or p.entry_price_eur, 4),
                'pnl_eur': round(pnl, 2),
                'pnl_pct': round(p.unrealized_pnl_pct(), 2),
            })
        initial_capital = account.portfolio.starting_capital if account.portfolio_id and account.portfolio else 10000.0
        return {
            'cash_eur': round(account.cash_eur, 2),
            'equity_eur': round(account.equity_eur, 2),
            'positions_value': round(pos_value, 2),
            'positions': positions,
            'total_return_pct': round((account.equity_eur - initial_capital) / initial_capital * 100, 2),
            'timestamp': datetime.now(timezone.utc).isoformat(),
        }


# ─── WebSocket Events ────────────────────────────────────────────────────────

@socketio.on('connect')
def on_connect():
    from flask_login import current_user
    from flask import session
    if not current_user.is_authenticated:
        return False
    log.info('Client verbunden: %s', current_user.username)
    from flask import current_app
    portfolio_id = session.get('active_portfolio_id')
    socketio.emit('portfolio_update', _get_portfolio_snapshot(
        current_app._get_current_object(), portfolio_id=portfolio_id
    ))


@socketio.on('disconnect')
def on_disconnect():
    log.info('Client getrennt')


@socketio.on('request_update')
def on_request_update():
    from flask_login import current_user
    from flask import session, current_app
    if not current_user.is_authenticated:
        return False
    portfolio_id = session.get('active_portfolio_id')
    socketio.emit('portfolio_update', _get_portfolio_snapshot(
        current_app._get_current_object(), portfolio_id=portfolio_id
    ))


if __name__ == '__main__':
    app = create_app()
    socketio.run(app, host='0.0.0.0', port=5000, debug=config.DEBUG)
