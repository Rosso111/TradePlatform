"""strategy_slug_mode_approved

Revision ID: 0004_strategy_slug_mode
Revises: 0003_sim_user_id
Create Date: 2026-04-30

Implements: S-01, S-02, DB-04, DB-06
Ergänzt Strategy-Tabelle um slug, mode, is_approved_live.
Migriert bestehende Strategien aus data/strategies.json als is_system=True.
"""
from alembic import op
import sqlalchemy as sa


revision = '0004_strategy_slug_mode'
down_revision = '0003_sim_user_id'
branch_labels = None
depends_on = None


def upgrade():
    # Schema-Änderungen
    with op.batch_alter_table('strategies', schema=None) as batch_op:
        batch_op.add_column(sa.Column('slug', sa.String(80), nullable=True))
        batch_op.add_column(sa.Column('mode', sa.String(40), nullable=False, server_default='score'))
        batch_op.add_column(sa.Column('is_approved_live', sa.Boolean(), nullable=False, server_default='false'))

    # Datenmigration aus JSON — Implements: DB-06
    import json
    from pathlib import Path
    conn = op.get_bind()

    # Idempotenz: Nur migrieren wenn noch keine System-Strategien in der DB
    count = conn.execute(sa.text("SELECT COUNT(*) FROM strategies WHERE is_system=true")).scalar()
    if count > 0:
        return

    strategies_file = Path(__file__).resolve().parent.parent.parent / 'data' / 'strategies.json'
    if not strategies_file.exists():
        return

    data = json.loads(strategies_file.read_text(encoding='utf-8'))
    approved = set(data.get('approved_live_strategies', []))

    for s in data.get('strategies', []):
        sid = s.get('id')
        if not sid:
            continue
        conn.execute(sa.text("""
            INSERT INTO strategies (user_id, name, description, is_system, params, slug, mode, is_approved_live, created_at)
            VALUES (NULL, :name, :desc, true, :params::jsonb, :slug, :mode, :approved, NOW())
            ON CONFLICT DO NOTHING
        """), {
            'name': s.get('name', sid),
            'desc': s.get('description', ''),
            'params': json.dumps(s.get('params', {})),
            'slug': sid,
            'mode': s.get('mode', 'score'),
            'approved': sid in approved,
        })

    # UniqueConstraint auf slug (nach Datenmigration)
    with op.batch_alter_table('strategies', schema=None) as batch_op:
        batch_op.create_unique_constraint('uq_strategies_slug', ['slug'])


def downgrade():
    with op.batch_alter_table('strategies', schema=None) as batch_op:
        batch_op.drop_constraint('uq_strategies_slug', type_='unique')
        batch_op.drop_column('is_approved_live')
        batch_op.drop_column('mode')
        batch_op.drop_column('slug')
