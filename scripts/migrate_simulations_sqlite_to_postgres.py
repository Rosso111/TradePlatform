import json
import os
import sqlite3
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import psycopg
from dotenv import load_dotenv
from psycopg.types.json import Json

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / '.env')

SQLITE_PATH = os.environ.get('SQLITE_PATH', 'data/trading.db')
PG_HOST = os.environ['POSTGRES_HOST']
PG_PORT = int(os.environ.get('POSTGRES_PORT', '5432'))
PG_DB = os.environ['POSTGRES_DB']
PG_USER = os.environ['POSTGRES_USER']
PG_PASSWORD = os.environ['POSTGRES_PASSWORD']
PG_SSLMODE = os.environ.get('POSTGRES_SSLMODE', 'prefer')

SIM_TABLES = [
    'simulation_runs',
    'simulation_positions',
    'decision_logs',
    'simulation_trades',
    'simulation_daily_snapshots',
]

BOOLEAN_COLUMNS = {
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


def pg_columns(cur, table):
    return [r[0] for r in cur.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = %s
        ORDER BY ordinal_position
        """,
        (table,),
    ).fetchall()]


def fetch_sqlite_rows(src, table, where_clause='', params=()):
    sql = f'SELECT * FROM {table}'
    if where_clause:
        sql += ' ' + where_clause
    return src.execute(sql, params).fetchall()


def sync_sequence(cur, table):
    seq_name = f'{table}_id_seq'
    try:
        cur.execute(
            f'SELECT setval(%s, COALESCE((SELECT MAX(id) FROM "{table}"), 1), true)',
            (seq_name,),
        )
    except Exception:
        pass


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
            sqlite_runs = fetch_sqlite_rows(src, 'simulation_runs', 'ORDER BY id')
            sqlite_run_ids = [row['id'] for row in sqlite_runs]
            if not sqlite_run_ids:
                print('simulation_runs: 0 rows in SQLite')
                return

            pg_run_ids = set()
            cur.execute('SELECT id FROM simulation_runs')
            for row in cur.fetchall():
                pg_run_ids.add(row[0])

            missing_run_ids = [run_id for run_id in sqlite_run_ids if run_id not in pg_run_ids]
            if not missing_run_ids:
                print('No missing simulation runs found. PostgreSQL already contains all SQLite run ids.')
                return

            print(f'Missing simulation run ids: {missing_run_ids}')

            for table in SIM_TABLES:
                cols = pg_columns(cur, table)
                if not cols:
                    print(f'{table}: skipped (table missing in PostgreSQL)')
                    continue

                placeholders = ', '.join(['%s'] * len(cols))
                quoted_cols = ', '.join(f'"{c}"' for c in cols)
                conflict_sql = ' ON CONFLICT (id) DO NOTHING' if 'id' in cols else ''
                insert_sql = f'INSERT INTO "{table}" ({quoted_cols}) VALUES ({placeholders}){conflict_sql}'

                if table == 'simulation_runs':
                    rows = [row for row in sqlite_runs if row['id'] in missing_run_ids]
                else:
                    qmarks = ','.join(['?'] * len(missing_run_ids))
                    rows = fetch_sqlite_rows(
                        src,
                        table,
                        f'WHERE run_id IN ({qmarks}) ORDER BY id',
                        tuple(missing_run_ids),
                    )

                if not rows:
                    print(f'{table}: 0 rows to migrate')
                    continue

                src_cols = [col for col in rows[0].keys() if col in cols]
                if src_cols != cols:
                    quoted_cols = ', '.join(f'"{c}"' for c in src_cols)
                    placeholders = ', '.join(['%s'] * len(src_cols))
                    conflict_sql = ' ON CONFLICT (id) DO NOTHING' if 'id' in src_cols else ''
                    insert_sql = f'INSERT INTO "{table}" ({quoted_cols}) VALUES ({placeholders}){conflict_sql}'

                data = [tuple(normalize(table, col, row[col]) for col in src_cols) for row in rows]
                cur.executemany(insert_sql, data)
                sync_sequence(cur, table)
                skipped_cols = [col for col in rows[0].keys() if col not in src_cols]
                if skipped_cols:
                    print(f'{table}: migrated {len(rows)} rows (skipped columns: {", ".join(skipped_cols)})')
                else:
                    print(f'{table}: migrated {len(rows)} rows')

        pg.commit()

    src.close()
    print('Simulation migration complete.')


if __name__ == '__main__':
    main()
