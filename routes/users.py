"""
User-Management Routes
Admin-only CRUD für User-Verwaltung.
"""
from functools import wraps
from flask import Blueprint, jsonify, request
from flask_login import login_required, current_user
import logging

from models import db, User

log = logging.getLogger(__name__)
users_bp = Blueprint('users', __name__, url_prefix='/api/users')

_MIN_PASSWORD_LEN = 10


def _validate_password(pw: str):
    if len(pw) < _MIN_PASSWORD_LEN:
        return f'Passwort muss mindestens {_MIN_PASSWORD_LEN} Zeichen haben.'
    return None


def admin_required(f):
    """Decorator: Route nur für eingeloggte Admins zugänglich."""
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        if current_user.role != 'admin':
            return jsonify({'error': 'Administratorrechte erforderlich'}), 403
        return f(*args, **kwargs)
    return decorated


@users_bp.route('', methods=['GET'])
@admin_required
def list_users():
    """Implements: U-06"""
    users = User.query.order_by(User.id).all()
    return jsonify([u.to_dict() for u in users])


@users_bp.route('', methods=['POST'])
@admin_required
def create_user():
    """Implements: U-06"""
    data = request.get_json(silent=True) or {}
    username = data.get('username', '').strip()
    email = data.get('email', '').strip() or None
    password = data.get('password', '')
    role = data.get('role', 'user')

    if not username or not password:
        return jsonify({'error': 'Username und Passwort erforderlich'}), 400

    if role not in ('admin', 'user'):
        return jsonify({'error': 'Ungültige Rolle (admin|user)'}), 400

    if User.query.filter_by(username=username).first():
        return jsonify({'error': 'Username bereits vergeben'}), 409

    if email and User.query.filter_by(email=email).first():
        return jsonify({'error': 'E-Mail bereits vergeben'}), 409

    if err := _validate_password(password):
        return jsonify({'error': err}), 400

    user = User(username=username, email=email, role=role)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    log.info("Admin '%s' hat User '%s' angelegt.", current_user.username, username)
    return jsonify(user.to_dict()), 201


@users_bp.route('/<int:user_id>', methods=['PUT'])
@admin_required
def update_user(user_id):
    """Username, E-Mail und Rolle ändern. Passwort hat einen eigenen Endpoint."""
    user = User.query.get_or_404(user_id)
    data = request.get_json(silent=True) or {}

    new_username = data.get('username', '').strip()
    new_email = data.get('email', '').strip() or None
    new_role = data.get('role')

    if new_username and new_username != user.username:
        if User.query.filter_by(username=new_username).first():
            return jsonify({'error': 'Username bereits vergeben'}), 409
        user.username = new_username

    if 'email' in data:
        if new_email and new_email != user.email:
            if User.query.filter_by(email=new_email).first():
                return jsonify({'error': 'E-Mail bereits vergeben'}), 409
        user.email = new_email

    if new_role is not None:
        if new_role not in ('admin', 'user'):
            return jsonify({'error': 'Ungültige Rolle (admin|user)'}), 400
        user.role = new_role

    db.session.commit()
    return jsonify(user.to_dict())


@users_bp.route('/<int:user_id>/status', methods=['PATCH'])
@admin_required
def toggle_user_status(user_id):
    """Implements: U-07 — User aktivieren oder deaktivieren."""
    user = User.query.get_or_404(user_id)

    # Admin darf sich nicht selbst deaktivieren
    if user.id == current_user.id:
        return jsonify({'error': 'Eigenes Konto kann nicht deaktiviert werden'}), 400

    user.is_active = not user.is_active
    db.session.commit()
    log.info(
        "Admin '%s' hat User '%s' %s.",
        current_user.username, user.username,
        'aktiviert' if user.is_active else 'deaktiviert'
    )
    return jsonify(user.to_dict())


@users_bp.route('/<int:user_id>/password', methods=['PUT'])
@login_required
def change_password(user_id):
    """Implements: U-08 (Admin setzt Passwort zurück), U-09 (User ändert eigenes Passwort).

    Admin: kein altes Passwort nötig.
    Eigenes Passwort: old_password muss korrekt sein.
    """
    user = User.query.get_or_404(user_id)
    data = request.get_json(silent=True) or {}
    new_password = data.get('new_password', '')

    if not new_password:
        return jsonify({'error': 'Neues Passwort erforderlich'}), 400

    is_own_account = (user.id == current_user.id)
    is_admin = (current_user.role == 'admin')

    if not is_own_account and not is_admin:
        return jsonify({'error': 'Keine Berechtigung'}), 403

    if is_own_account and not is_admin:
        # Normaler User muss altes Passwort bestätigen
        old_password = data.get('old_password', '')
        if not old_password or not user.check_password(old_password):
            return jsonify({'error': 'Altes Passwort ist falsch'}), 401

    if err := _validate_password(new_password):
        return jsonify({'error': err}), 400

    user.set_password(new_password)
    db.session.commit()
    log.info(
        "Passwort für User '%s' wurde von '%s' geändert.",
        user.username, current_user.username
    )
    return jsonify({'message': 'Passwort erfolgreich geändert'})
