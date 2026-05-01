"""data_migration_default_portfolio

Revision ID: 0002_data_migration
Revises: 650d557d5156
Create Date: 2026-04-30

Implements: G-06, DB-01, DB-02, DB-04, DB-05
Weist alle bestehenden Datensätze mit portfolio_id=NULL dem Default-Portfolio
des Admin-Users zu. Erstellt das Default-Portfolio falls noch keines existiert.
"""
from alembic import op
import sqlalchemy as sa


revision = '0002_data_migration'
down_revision = '650d557d5156'
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()

    # 1. Admin-User finden
    admin = conn.execute(
        sa.text("SELECT id FROM users WHERE role='admin' ORDER BY id LIMIT 1")
    ).fetchone()

    if admin is None:
        # Kein User existiert — Frisch-Installation, keine Daten zu migrieren
        return

    admin_id = admin[0]

    # 2. Default-Portfolio für Admin anlegen, falls noch keines existiert
    existing = conn.execute(
        sa.text("SELECT id FROM portfolios WHERE user_id=:uid ORDER BY id LIMIT 1"),
        {'uid': admin_id}
    ).fetchone()

    if existing:
        portfolio_id = existing[0]
    else:
        conn.execute(
            sa.text("""
                INSERT INTO portfolios (user_id, name, type, mode, status, currency,
                                        starting_capital, created_at)
                VALUES (:uid, 'Default', 'sim', 'auto', 'active', 'EUR',
                        10000.0, NOW())
            """),
            {'uid': admin_id}
        )
        portfolio_id = conn.execute(
            sa.text("SELECT id FROM portfolios WHERE user_id=:uid ORDER BY id LIMIT 1"),
            {'uid': admin_id}
        ).fetchone()[0]

    # 3. Account verknüpfen (portfolio_id = NULL → portfolio_id)
    conn.execute(
        sa.text("UPDATE account SET portfolio_id=:pid WHERE portfolio_id IS NULL"),
        {'pid': portfolio_id}
    )

    # 4. EquityHistory — Duplikate zuerst entfernen (UniqueConstraint portfolio_id+date)
    #    Behalte pro Datum den Eintrag mit der höchsten ID, lösche ältere Duplikate
    conn.execute(sa.text("""
        DELETE FROM equity_history
        WHERE portfolio_id IS NULL
          AND id NOT IN (
              SELECT MAX(id) FROM equity_history
              WHERE portfolio_id IS NULL
              GROUP BY date
          )
    """))
    conn.execute(
        sa.text("UPDATE equity_history SET portfolio_id=:pid WHERE portfolio_id IS NULL"),
        {'pid': portfolio_id}
    )

    # 5. Positions, Trades, Signals
    for table in ('positions', 'trades', 'signals'):
        conn.execute(
            sa.text(f"UPDATE {table} SET portfolio_id=:pid WHERE portfolio_id IS NULL"),
            {'pid': portfolio_id}
        )


def downgrade():
    # Implements: DB-05
    conn = op.get_bind()
    for table in ('account', 'equity_history', 'positions', 'trades', 'signals'):
        conn.execute(sa.text(f"UPDATE {table} SET portfolio_id=NULL"))
    # Portfolio selbst bleibt erhalten (Datenverlust-Schutz DB-01)
