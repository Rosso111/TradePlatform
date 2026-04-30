"""add_multi_user_portfolio_schema

Revision ID: 650d557d5156
Revises:
Create Date: 2026-04-30 05:39:50.702504

Implements: DB-01 bis DB-06
- Neue Tabellen: users, strategies, strategy_rules, portfolios, daily_proposals, proposed_orders
  (wurden bereits von db.create_all() angelegt — migration registriert nur den Ausgangszustand)
- Geänderte Tabellen: account, positions, trades, signals, equity_history + portfolio_id
- equity_history: unique(date) → unique(portfolio_id, date)
"""
from alembic import op
import sqlalchemy as sa


revision = '650d557d5156'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('account', schema=None) as batch_op:
        batch_op.add_column(sa.Column('portfolio_id', sa.Integer(), nullable=True))
        batch_op.create_unique_constraint('uq_account_portfolio_id', ['portfolio_id'])
        batch_op.create_foreign_key('fk_account_portfolio', 'portfolios', ['portfolio_id'], ['id'])

    with op.batch_alter_table('equity_history', schema=None) as batch_op:
        batch_op.add_column(sa.Column('portfolio_id', sa.Integer(), nullable=True))
        batch_op.drop_constraint('equity_history_date_key', type_='unique')
        batch_op.create_unique_constraint('uq_equity_portfolio_date', ['portfolio_id', 'date'])
        batch_op.create_foreign_key('fk_equity_history_portfolio', 'portfolios', ['portfolio_id'], ['id'])

    with op.batch_alter_table('positions', schema=None) as batch_op:
        batch_op.add_column(sa.Column('portfolio_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key('fk_positions_portfolio', 'portfolios', ['portfolio_id'], ['id'])

    with op.batch_alter_table('signals', schema=None) as batch_op:
        batch_op.add_column(sa.Column('portfolio_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key('fk_signals_portfolio', 'portfolios', ['portfolio_id'], ['id'])

    with op.batch_alter_table('trades', schema=None) as batch_op:
        batch_op.add_column(sa.Column('portfolio_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key('fk_trades_portfolio', 'portfolios', ['portfolio_id'], ['id'])

    # Implements: DB-02 — Bestehende Datensätze dem Default-Portfolio (id=1) zuweisen
    # wird nach der Migration in _init_default_portfolio() erledigt


def downgrade():
    with op.batch_alter_table('trades', schema=None) as batch_op:
        batch_op.drop_constraint('fk_trades_portfolio', type_='foreignkey')
        batch_op.drop_column('portfolio_id')

    with op.batch_alter_table('signals', schema=None) as batch_op:
        batch_op.drop_constraint('fk_signals_portfolio', type_='foreignkey')
        batch_op.drop_column('portfolio_id')

    with op.batch_alter_table('positions', schema=None) as batch_op:
        batch_op.drop_constraint('fk_positions_portfolio', type_='foreignkey')
        batch_op.drop_column('portfolio_id')

    with op.batch_alter_table('equity_history', schema=None) as batch_op:
        batch_op.drop_constraint('fk_equity_history_portfolio', type_='foreignkey')
        batch_op.drop_constraint('uq_equity_portfolio_date', type_='unique')
        batch_op.create_unique_constraint('equity_history_date_key', ['date'])
        batch_op.drop_column('portfolio_id')

    with op.batch_alter_table('account', schema=None) as batch_op:
        batch_op.drop_constraint('fk_account_portfolio', type_='foreignkey')
        batch_op.drop_constraint('uq_account_portfolio_id', type_='unique')
        batch_op.drop_column('portfolio_id')
