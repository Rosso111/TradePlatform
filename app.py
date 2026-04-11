"""
Flask Application Factory
Initialisiert App, Datenbank, WebSocket und den autonomen Scheduler.
"""
import logging
import os
from datetime import datetime, timezone

from flask import Flask, render_template
from flask_socketio import SocketIO
from flask_cors import CORS
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

import config
from models import db, Account

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)
log = logging.getLogger(__name__)

socketio = SocketIO()
scheduler = BackgroundScheduler(timezone='Europe/Vienna')


def create_app():
    app = Flask(__name__)
    app.config['SECRET_KEY'] = config.SECRET_KEY
    app.config['SQLALCHEMY_DATABASE_URI'] = config.SQLALCHEMY_DATABASE_URI
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

    os.makedirs(os.path.dirname(config.DATABASE_PATH), exist_ok=True)

    # Extensions
    db.init_app(app)
    CORS(app)
    socketio.init_app(app, cors_allowed_origins='*', async_mode='eventlet')

    # Blueprints
    from routes.api import api
    app.register_blueprint(api)

    # Hauptseite
    @app.route('/')
    def index():
        return render_template('index.html')

    # Datenbank & Startdaten initialisieren
    with app.app_context():
        db.create_all()
        _init_account()
        log.info("Datenbank initialisiert.")

    # Scheduler starten
    _setup_scheduler(app)

    # Initialer Datenladevorgang (im Hintergrund)
    _initial_data_load(app)

    return app


def _init_account():
    """Erstellt das Konto falls es nicht existiert."""
    if not Account.query.first():
        account = Account(
            cash_eur=config.STARTING_CAPITAL,
            equity_eur=config.STARTING_CAPITAL,
        )
        db.session.add(account)
        db.session.commit()
        log.info(f"Neues Konto erstellt: {config.STARTING_CAPITAL} EUR Startkapital")


def _setup_scheduler(app):
    """Richtet den autonomen Handelstakt ein."""

    def trading_job():
        from services.trading_engine import run_trading_cycle
        try:
            actions = run_trading_cycle(app)
            if actions:
                socketio.emit('trading_actions', {'actions': actions})
            socketio.emit('portfolio_update', _get_portfolio_snapshot(app))
        except Exception as e:
            log.error(f"Scheduler-Fehler: {e}")

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

    scheduler.start()
    log.info(f"Scheduler gestartet. Handelszyklus alle {config.TRADING_INTERVAL_MINUTES} Minuten.")


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


def _get_portfolio_snapshot(app) -> dict:
    """Snapshot des aktuellen Portfolio-Stands für WebSocket-Push."""
    with app.app_context():
        account = Account.query.first()
        if not account:
            return {}
        positions = []
        pos_value = 0.0
        for p in __import__('models').Position.query.all():
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

        return {
            'cash_eur': round(account.cash_eur, 2),
            'equity_eur': round(account.equity_eur, 2),
            'positions_value': round(pos_value, 2),
            'positions': positions,
            'total_return_pct': round((account.equity_eur - 10000.0) / 10000.0 * 100, 2),
            'timestamp': datetime.now(timezone.utc).isoformat(),
        }


# ─── WebSocket Events ────────────────────────────────────────────────────────

@socketio.on('connect')
def on_connect():
    log.info('Client verbunden')
    from flask import current_app
    socketio.emit('portfolio_update', _get_portfolio_snapshot(current_app._get_current_object()))


@socketio.on('disconnect')
def on_disconnect():
    log.info('Client getrennt')


@socketio.on('request_update')
def on_request_update():
    from flask import current_app
    socketio.emit('portfolio_update', _get_portfolio_snapshot(current_app._get_current_object()))


if __name__ == '__main__':
    app = create_app()
    socketio.run(app, host='0.0.0.0', port=5000, debug=config.DEBUG)
