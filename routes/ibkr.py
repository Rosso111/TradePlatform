"""
IBKR Routes — Kontozugriff, Positionen und manuelle Orders für IBKR-Portfolios.
"""
from flask import Blueprint, jsonify, request
from flask_login import login_required, current_user
import logging

import config as _config
from models import Portfolio
from services.ibkr_connector import IBKRConnectionPool, OrderPendingError

log = logging.getLogger(__name__)
ibkr_bp = Blueprint('ibkr', __name__, url_prefix='/api/ibkr')


def _get_ibkr_portfolios():
    """Gibt IBKR-Portfolios des aktuellen Users (oder alle für Admins) zurück."""
    if current_user.role == 'admin':
        return Portfolio.query.filter(Portfolio.type.in_(('ibkr_paper', 'ibkr_live'))).all()
    return Portfolio.query.filter(
        Portfolio.user_id == current_user.id,
        Portfolio.type.in_(('ibkr_paper', 'ibkr_live')),
    ).all()


def _connector_for_portfolio(portfolio: Portfolio):
    port      = _config.IBKR_LIVE_PORT if portfolio.type == 'ibkr_live' else _config.IBKR_PAPER_PORT
    client_id = _config.IBKR_CLIENT_ID + 10  # UI-Verbindung nutzt andere ID als Trading-Engine
    return IBKRConnectionPool.get(_config.IBKR_HOST, port, client_id)


@ibkr_bp.route('/status', methods=['GET'])
@login_required
def ibkr_status():
    """Verbindungsstatus aller IBKR-Gateways."""
    portfolios = _get_ibkr_portfolios()
    if not portfolios:
        return jsonify({'connected': False, 'gateways': [], 'has_ibkr': False})

    gateways = IBKRConnectionPool.status()
    any_connected = any(g['connected'] for g in gateways)
    return jsonify({
        'has_ibkr': True,
        'connected': any_connected,
        'gateways': gateways,
        'portfolios': [
            {'id': p.id, 'name': p.name, 'type': p.type, 'account': p.ibkr_account_id or ''}
            for p in portfolios
        ],
    })


@ibkr_bp.route('/connect', methods=['POST'])
@login_required
def ibkr_connect():
    """Verbindung zum Gateway (neu) aufbauen."""
    portfolios = _get_ibkr_portfolios()
    if not portfolios:
        return jsonify({'error': 'Kein IBKR-Portfolio vorhanden'}), 400

    results = []
    seen_ports = set()
    for p in portfolios:
        port = _config.IBKR_LIVE_PORT if p.type == 'ibkr_live' else _config.IBKR_PAPER_PORT
        if port in seen_ports:
            continue
        seen_ports.add(port)
        conn = IBKRConnectionPool.get(_config.IBKR_HOST, port, _config.IBKR_CLIENT_ID)
        ok = conn.connect()
        results.append({'port': port, 'connected': ok})

    return jsonify({'results': results})


@ibkr_bp.route('/account', methods=['GET'])
@login_required
def ibkr_account():
    """Kontostand für ein bestimmtes IBKR-Portfolio."""
    portfolio_id = request.args.get('portfolio_id', type=int)
    portfolios   = _get_ibkr_portfolios()
    if not portfolios:
        return jsonify({'error': 'Kein IBKR-Portfolio vorhanden'}), 400

    portfolio = next((p for p in portfolios if p.id == portfolio_id), portfolios[0])
    conn = _connector_for_portfolio(portfolio)

    try:
        data = conn.get_account_values(portfolio.ibkr_account_id or '')
        return jsonify({'portfolio_id': portfolio.id, 'portfolio_name': portfolio.name, **data})
    except Exception as e:
        log.error('IBKR account query: %s', e)
        return jsonify({'error': str(e)}), 502


@ibkr_bp.route('/positions', methods=['GET'])
@login_required
def ibkr_positions():
    """Offene Positionen inkl. Kaufpreis, Marktkurs und P&L vom IBKR-Gateway."""
    portfolio_id = request.args.get('portfolio_id', type=int)
    portfolios   = _get_ibkr_portfolios()
    if not portfolios:
        return jsonify([])

    portfolio = next((p for p in portfolios if p.id == portfolio_id), portfolios[0])
    conn      = _connector_for_portfolio(portfolio)

    from models import Stock, Price

    # Portfolio-Items versuchen (enthält Marktkurse wenn Gateway Daten hat)
    items = []
    try:
        items = conn.get_portfolio_items(portfolio.ibkr_account_id or '')
    except Exception as e:
        log.warning('get_portfolio_items fehlgeschlagen, nutze get_positions: %s', e)

    # Fallback: einfache Positionsliste
    if not items:
        try:
            items = [dict(p, market_price=None, market_value=None, unrealized_pnl=None, pnl_pct=None)
                     for p in conn.get_positions(portfolio.ibkr_account_id or '')]
        except Exception as e:
            log.error('IBKR positions query: %s', e)
            return jsonify({'error': str(e)}), 502

    # Fehlende Marktkurse aus DB-Preisen auffüllen
    for item in items:
        if item.get('market_price'):
            continue
        stock = Stock.query.filter_by(symbol=item['symbol']).first()
        if not stock:
            continue
        latest = Price.query.filter_by(stock_id=stock.id).order_by(Price.date.desc()).first()
        if latest and latest.close:
            mkt = float(latest.close)
            avg = item.get('avg_cost') or 0
            qty = item.get('qty', 0)
            item['market_price']   = mkt
            item['market_value']   = round(mkt * qty, 2)
            item['unrealized_pnl'] = round((mkt - avg) * qty, 2) if avg else None
            item['pnl_pct']        = round((mkt - avg) / avg * 100, 2) if avg else None

    return jsonify(items)


@ibkr_bp.route('/gateway/start', methods=['POST'])
@login_required
def ibkr_gateway_start():
    if current_user.role != 'admin':
        return jsonify({'error': 'Administratorrechte erforderlich'}), 403
    """Startet den IB Gateway über den Systemd-Service."""
    import subprocess
    try:
        result = subprocess.run(
            ['systemctl', 'start', 'ibgateway'],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0:
            return jsonify({'success': True, 'message': 'ibgateway.service gestartet — Auto-Login läuft (~15s)'})
        # Fallback: direkt starten falls systemctl keine Rechte hat
        subprocess.Popen(
            ['/home/martin/ibgateway/run_gateway.sh'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        return jsonify({'success': True, 'message': 'Gateway direkt gestartet (run_gateway.sh)'})
    except Exception as e:
        log.error('Gateway-Start fehlgeschlagen: %s', e)
        return jsonify({'error': str(e)}), 500


@ibkr_bp.route('/gateway/stop', methods=['POST'])
@login_required
def ibkr_gateway_stop():
    if current_user.role != 'admin':
        return jsonify({'error': 'Administratorrechte erforderlich'}), 403
    """Stoppt den IB Gateway über den Systemd-Service."""
    import subprocess
    try:
        result = subprocess.run(
            ['systemctl', 'stop', 'ibgateway'],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0:
            return jsonify({'success': True, 'message': 'ibgateway.service gestoppt'})
        return jsonify({'error': result.stderr or 'Unbekannter Fehler'}), 500
    except Exception as e:
        log.error('Gateway-Stop fehlgeschlagen: %s', e)
        return jsonify({'error': str(e)}), 500


@ibkr_bp.route('/gateway/status', methods=['GET'])
@login_required
def ibkr_gateway_process_status():
    """Gibt zurück ob der Systemd-Service aktiv ist."""
    import subprocess
    try:
        result = subprocess.run(
            ['systemctl', 'is-active', 'ibgateway'],
            capture_output=True, text=True, timeout=5,
        )
        active = result.stdout.strip() == 'active'
        return jsonify({'active': active, 'state': result.stdout.strip()})
    except Exception as e:
        return jsonify({'active': False, 'state': 'unknown', 'error': str(e)})


@ibkr_bp.route('/signals', methods=['GET'])
@login_required
def ibkr_signals():
    """Aktuelle BUY-Signale aus der DB, sortiert nach Score."""
    from models import Signal, Stock, Price
    from datetime import date, timedelta

    portfolio_id = request.args.get('portfolio_id', type=int)
    portfolios   = _get_ibkr_portfolios()
    portfolio    = next((p for p in portfolios if p.id == portfolio_id), portfolios[0] if portfolios else None)

    # Offene Symbole direkt von IBKR holen (nicht aus DB — die kann veraltete/falsche Daten haben)
    open_symbols = set()
    if portfolio:
        try:
            conn = _connector_for_portfolio(portfolio)
            live_pos = conn.get_positions(portfolio.ibkr_account_id or '')
            open_symbols = {p['symbol'] for p in live_pos}
        except Exception:
            pass

    # Neueste Signale aus der DB (heute oder letzter verfügbarer Tag)
    today      = date.today()
    cutoff     = today - timedelta(days=5)
    latest_date = (
        Signal.query
        .filter(Signal.action == 'BUY', Signal.date >= cutoff)
        .order_by(Signal.date.desc())
        .with_entities(Signal.date)
        .first()
    )
    if not latest_date:
        return jsonify([])

    signals = (
        Signal.query
        .join(Stock)
        .filter(Signal.action == 'BUY', Signal.date == latest_date[0])
        .order_by(Signal.score.desc())
        .limit(30)
        .all()
    )

    result = []
    for s in signals:
        stock  = s.stock
        latest_price = (
            Price.query
            .filter_by(stock_id=stock.id)
            .order_by(Price.date.desc())
            .first()
        )
        result.append({
            'stock_id':    stock.id,
            'symbol':      stock.symbol,
            'name':        stock.name,
            'sector':      stock.sector,
            'score':       round(s.score, 1),
            'rsi':         round(s.rsi, 1) if s.rsi else None,
            'price':       round(latest_price.close, 2) if latest_price else None,
            'price_eur':   round(latest_price.close_eur, 2) if latest_price and latest_price.close_eur else None,
            'currency':    stock.currency,
            'date':        s.date.isoformat(),
            'in_portfolio': stock.symbol in open_symbols,
        })

    return jsonify(result)


@ibkr_bp.route('/order', methods=['POST'])
@login_required
def ibkr_order():
    """Manuellen Kauf oder Verkauf über IBKR ausführen."""
    payload      = request.get_json(silent=True) or {}
    portfolio_id = payload.get('portfolio_id')
    symbol       = (payload.get('symbol') or '').strip().upper()
    qty          = int(payload.get('qty', 0))
    action       = (payload.get('action') or '').strip().upper()

    if not symbol:
        return jsonify({'error': 'Symbol fehlt'}), 400
    if qty <= 0:
        return jsonify({'error': 'Stückzahl muss > 0 sein'}), 400
    if action not in ('BUY', 'SELL'):
        return jsonify({'error': 'action muss BUY oder SELL sein'}), 400

    portfolios = _get_ibkr_portfolios()
    if not portfolios:
        return jsonify({'error': 'Kein IBKR-Portfolio vorhanden'}), 400

    portfolio = next((p for p in portfolios if p.id == portfolio_id), portfolios[0])
    conn      = _connector_for_portfolio(portfolio)

    try:
        fill_price, fill_qty = conn.place_market_order(
            symbol, qty, action,
            account=portfolio.ibkr_account_id or '',
        )
        msg = f'{action} {fill_qty}x {symbol} @ ${fill_price:.2f} (Portfolio: {portfolio.name})'
        log.info('Manueller IBKR-Trade: %s', msg)
        return jsonify({'success': True, 'fill_price': fill_price, 'fill_qty': fill_qty, 'message': msg})
    except OrderPendingError as e:
        msg = f'{action} {qty}x {symbol} — Order in IBKR ausstehend (Status: {e.status}, füllt bei Marktöffnung)'
        log.info('Manueller IBKR-Trade ausstehend: %s', msg)
        return jsonify({'success': True, 'pending': True, 'status': e.status, 'message': msg}), 202
    except Exception as e:
        log.error('IBKR manual order %s %s: %s', action, symbol, e)
        return jsonify({'error': str(e)}), 502
