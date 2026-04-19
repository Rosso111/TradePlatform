# Postgres Migration

## 1. Environment

Set these variables before starting the app:

```bash
export DB_BACKEND=postgres
export POSTGRES_HOST=192.168.0.165
export POSTGRES_PORT=5432
export POSTGRES_DB=Tradebot
export POSTGRES_USER=openclaw
export POSTGRES_PASSWORD='REPLACE_ME'
export POSTGRES_SSLMODE=prefer
```

## 2. Install dependency

```bash
pip install -r requirements.txt
```

## 3. Create schema

Start the app once so `db.create_all()` creates the tables in Postgres.

## 4. Migrate SQLite data

```bash
export SQLITE_PATH=data/trading.db
python scripts/migrate_sqlite_to_postgres.py
```

## 5. Restart app

After migration, restart the app and verify:
- dashboard loads
- simulations load
- a new replay run starts successfully

## 6. Verify performance indexes

The app now attempts to create a few important Postgres indexes automatically at startup.

You can verify them manually with:

```sql
SELECT indexname, indexdef
FROM pg_indexes
WHERE schemaname = 'public'
  AND indexname IN (
    'idx_prices_stock_date_desc',
    'idx_decision_logs_run_date_id',
    'idx_decision_logs_run_executed',
    'idx_simulation_trades_run_date_id',
    'idx_simulation_positions_run_stock',
    'idx_simulation_daily_snapshots_run_date_desc'
  )
ORDER BY indexname;
```

## Notes
- Rotate the database password after setup if it was pasted into chat.
- If staging should also use Postgres, point its env vars to a separate DB.
- These indexes are aimed mainly at historical replay and simulation detail API performance.
