"""
Shared fixtures for the TradePlatform test suite.
"""
import os

# Must be set before importing config or app — satisfies config.py validation
os.environ.setdefault('POSTGRES_HOST', 'localhost')
os.environ.setdefault('POSTGRES_DB', 'test_db')
os.environ.setdefault('POSTGRES_USER', 'test_user')
os.environ.setdefault('POSTGRES_PASSWORD', 'test_pass')

import pytest
from datetime import date, datetime, timezone

from app import create_app
from models import db as _db, User, Portfolio, Account, Stock, Position, DailyProposal, ProposedOrder

TEST_CONFIG = {
    'TESTING': True,
    'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:',
    'SQLALCHEMY_ENGINE_OPTIONS': {},
    'SQLALCHEMY_TRACK_MODIFICATIONS': False,
    'SECRET_KEY': 'test-secret-key-not-for-production',
    'WTF_CSRF_ENABLED': False,
    'RATELIMIT_ENABLED': False,
    'RATELIMIT_STORAGE_URI': 'memory://',
    'SERVER_NAME': None,
}


# ---------------------------------------------------------------------------
# App / DB fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope='session')
def app():
    application = create_app(test_config=TEST_CONFIG)
    with application.app_context():
        _db.create_all()
        yield application
        _db.drop_all()


@pytest.fixture(scope='function')
def db(app):
    with app.app_context():
        yield _db
        _db.session.rollback()
        for table in reversed(_db.metadata.sorted_tables):
            _db.session.execute(table.delete())
        _db.session.commit()


@pytest.fixture(scope='function')
def client(app):
    return app.test_client()


# ---------------------------------------------------------------------------
# Auth helper
# ---------------------------------------------------------------------------

def login(client, username, password):
    return client.post('/api/auth/login', json={'username': username, 'password': password})


# ---------------------------------------------------------------------------
# User fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def admin_user(db):
    u = User(username='admin', email='admin@test.com', role='admin', is_active=True)
    u.set_password('adminpassword1')
    db.session.add(u)
    db.session.commit()
    return u


@pytest.fixture
def regular_user(db):
    u = User(username='testuser', email='user@test.com', role='user', is_active=True)
    u.set_password('userpassword1')
    db.session.add(u)
    db.session.commit()
    return u


@pytest.fixture
def second_user(db):
    u = User(username='otheruser', email='other@test.com', role='user', is_active=True)
    u.set_password('otherpassword1')
    db.session.add(u)
    db.session.commit()
    return u


# ---------------------------------------------------------------------------
# Authenticated clients
# ---------------------------------------------------------------------------

@pytest.fixture
def admin_client(client, admin_user):
    login(client, 'admin', 'adminpassword1')
    return client


@pytest.fixture
def user_client(client, regular_user):
    login(client, 'testuser', 'userpassword1')
    return client


# ---------------------------------------------------------------------------
# Portfolio / Account fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def portfolio(db, regular_user):
    p = Portfolio(
        user_id=regular_user.id,
        name='Test Portfolio',
        type='sim',
        mode='auto',
        status='active',
        currency='EUR',
        starting_capital=10000.0,
    )
    db.session.add(p)
    db.session.flush()
    acc = Account(portfolio_id=p.id, cash_eur=10000.0, equity_eur=10000.0)
    db.session.add(acc)
    db.session.commit()
    return p


@pytest.fixture
def approval_portfolio(db, regular_user):
    p = Portfolio(
        user_id=regular_user.id,
        name='Approval Portfolio',
        type='sim',
        mode='approval',
        status='active',
        currency='EUR',
        starting_capital=10000.0,
    )
    db.session.add(p)
    db.session.flush()
    acc = Account(portfolio_id=p.id, cash_eur=10000.0, equity_eur=10000.0)
    db.session.add(acc)
    db.session.commit()
    return p


@pytest.fixture
def admin_portfolio(db, admin_user):
    p = Portfolio(
        user_id=admin_user.id,
        name='Admin Portfolio',
        type='sim',
        mode='auto',
        status='active',
        currency='EUR',
        starting_capital=10000.0,
    )
    db.session.add(p)
    db.session.flush()
    acc = Account(portfolio_id=p.id, cash_eur=10000.0, equity_eur=10000.0)
    db.session.add(acc)
    db.session.commit()
    return p


# ---------------------------------------------------------------------------
# Stock / Proposal fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def stock(db):
    s = Stock(symbol='AAPL', name='Apple Inc.', sector='Technology',
              region='US', currency='USD', active=True)
    db.session.add(s)
    db.session.commit()
    return s


@pytest.fixture
def proposal(db, approval_portfolio, stock):
    """Ein offener DailyProposal mit einer BUY-Order (approved=True)."""
    p = DailyProposal(
        portfolio_id=approval_portfolio.id,
        proposal_date=date.today(),
        status='open',
    )
    db.session.add(p)
    db.session.flush()
    order = ProposedOrder(
        proposal_id=p.id,
        stock_id=stock.id,
        action='BUY',
        shares_proposed=10.0,
        est_price_eur=150.0,
        score=75.0,
        reason='Test reason',
        approved=True,
    )
    db.session.add(order)
    db.session.commit()
    return p
