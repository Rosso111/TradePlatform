"""simulation_run_user_id

Revision ID: 0003_sim_user_id
Revises: 0002_data_migration
Create Date: 2026-04-30

Implements: G-02, DB-04, DB-05
Fuegt simulation_runs.user_id hinzu und ordnet bestehende Runs dem Admin-User zu.
"""
from alembic import op
import sqlalchemy as sa


revision = '0003_sim_user_id'
down_revision = '0002_data_migration'
branch_labels = None
depends_on = None


def upgrade():
    # Implements: G-02, DB-04
    with op.batch_alter_table('simulation_runs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('user_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key('fk_sim_runs_user', 'users', ['user_id'], ['id'])

    # Bestehende Runs dem Admin-User zuordnen
    conn = op.get_bind()
    admin = conn.execute(
        sa.text("SELECT id FROM users WHERE role='admin' ORDER BY id LIMIT 1")
    ).fetchone()
    if admin:
        conn.execute(
            sa.text("UPDATE simulation_runs SET user_id=:uid WHERE user_id IS NULL"),
            {'uid': admin[0]}
        )


def downgrade():
    # Implements: DB-05
    with op.batch_alter_table('simulation_runs', schema=None) as batch_op:
        batch_op.drop_constraint('fk_sim_runs_user', type_='foreignkey')
        batch_op.drop_column('user_id')
