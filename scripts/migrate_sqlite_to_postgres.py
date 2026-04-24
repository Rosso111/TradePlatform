import json
import os
import sqlite3
from datetime import date, datetime
from decimal import Decimal

import psycopg
from psycopg.types.json import Json

SQLITE_PATH = os.environ.get('SQLITE_PATH', 'data/trading.db')
PG_HOST = os.environ['POSTGRES_HOST']
PG_PORT = int(os.environ.get('POSTGRES_PORT', '5432'))
PG_DB = os.environ['POSTGRES_DB']
PG_USER = os.environ['POSTGRES_USER']
PG_PASSWORD = os.environ['POSTGRES_PASSWORD']
PG_SSLMODE = os.environ.get('POSTGRES_SSLMODE', 'prefer')

TABLES = [
    'stocks',
    'exchange_rates',
    'account',
    'prices',
    'positions',
    'trades',
    'signals',
    'algo_params',
    'equity_history',
    'simulation_runs',
    'simulation_positions',
    'decision_logs',
    'simulation_trades',
    'simulation_daily_snapshots',
]

BOOLEAN_COLUMNS = {
    ('stocks', 'active'),
    ('decision_logs', 'executed'),
}

JSON_COLUMNS = {
    ('decision_logs', 'reason_json'),
    ('decision_logs', 'risk_json'),
    ('decision_logs', 'data_snapshot_json'),
}


def normalize(table, column, value):
    if value is None:
        return None
    if (table, column) in BOOLEAN_COLUMNS:
        return bool(value)
    if (table, column) in JSON_COLUMNS:
        if value in ('', None):
            return None
        if isinstance(value, str):
            try:
                return Json(json.loads(value))
            except Exception:
                return Json(value)
        return Json(value)
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat(sep=' ')
    return value


def main():
    src = sqlite3.connect(SQLITE_PATH)
    src.row_factory = sqlite3.Row

    with psycopg.connect(
        host=PG_HOST,
        port=PG_PORT,
        dbname=PG_DB,
        user=PG_USER,
        password=PG_PASSWORD,
        sslmode=PG_SSLMODE,
    ) as pg:
        with pg.cursor() as cur:
            for table in TABLES:
                rows = src.execute(f'SELECT * FROM {table}').fetchall()
                if not rows:
                    print(f'{table}: 0 rows')
                    continue

                pg_cols = [r[0] for r in cur.execute(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema = 'public' AND table_name = %s
                    ORDER BY ordinal_position
                    """,
                    (table,),
                ).fetchall()]
                if not pg_cols:
                    print(f'{table}: skipped (table missing in Postgres)')
                    continue

                src_cols = [col for col in rows[0].keys() if col in pg_cols]
                quoted_cols = ', '.join(f'"{c}"' for c in src_cols)
                placeholders = ', '.join(['%s'] * len(src_cols))
                cur.execute(f'TRUNCATE TABLE "{table}" RESTART IDENTITY CASCADE;')
                insert_sql = f'INSERT INTO "{table}" ({quoted_cols}) VALUES ({placeholders})'
                data = [tuple(normalize(table, col, row[col]) for col in src_cols) for row in rows]
                cur.executemany(insert_sql, data)
                if 'id' in src_cols:
                    seq_name = f'{table}_id_seq'
                    try:
                        cur.execute(
                            f'SELECT setval(%s, COALESCE((SELECT MAX(id) FROM "{table}"), 1), true)',
                            (seq_name,),
                        )
                    except Exception:
                        pass
                skipped_cols = [col for col in rows[0].keys() if col not in src_cols]
                if skipped_cols:
                    print(f'{table}: {len(rows)} rows (skipped columns: {', '.join(skipped_cols)})')
                else:
                    print(f'{table}: {len(rows)} rows')
        pg.commit()

    src.close()
    print('Migration complete.')


if __name__ == '__main__':
    main()
