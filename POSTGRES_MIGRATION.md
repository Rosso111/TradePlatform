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

## Notes
- Rotate the database password after setup if it was pasted into chat.
- If staging should also use Postgres, point its env vars to a separate DB.
