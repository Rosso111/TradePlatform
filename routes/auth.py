"""
Auth Routes
Login, Logout und Session-Prüfung für das Multi-User-System.
"""
from flask import Blueprint, jsonify, request, session
from flask_login import login_user, logout_user, login_required, current_user
import logging

from app import limiter
from models import db, User, Portfolio

log = logging.getLogger(__name__)
auth_bp = Blueprint('auth', __name__, url_prefix='/api/auth')


@auth_bp.route('/login', methods=['POST'])
@limiter.limit("10 per minute; 30 per hour")
def login():
    """Implements: U-01, U-02"""
    data = request.get_json(silent=True) or {}
    username = data.get('username', '').strip()
    password = data.get('password', '')

    if not username or not password:
        return jsonify({'error': 'Username und Passwort erforderlich'}), 400

    user = User.query.filter_by(username=username).first()

    if not user or not user.is_active or not user.check_password(password):
        return jsonify({'error': 'Ungültige Anmeldedaten'}), 401

    login_user(user)
    # Implements: G-02, P-05
    portfolio = Portfolio.query.filter_by(
        user_id=user.id, status='active'
    ).order_by(Portfolio.id).first()
    if portfolio:
        session['active_portfolio_id'] = portfolio.id
    log.info("User '%s' eingeloggt.", user.username)
    return jsonify(user.to_dict())


@auth_bp.route('/logout', methods=['POST'])
def logout():
    """Implements: U-02"""
    logout_user()
    return jsonify({'message': 'Erfolgreich ausgeloggt'})


@auth_bp.route('/me', methods=['GET'])
@login_required
def me():
    """Implements: U-04"""
    portfolios = [p.to_dict() for p in current_user.portfolios.all()]
    result = current_user.to_dict()
    result['portfolios'] = portfolios
    return jsonify(result)
