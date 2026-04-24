# PostgreSQL-only Betrieb

Die Anwendung ist jetzt bewusst **PostgreSQL-only**. Ein SQLite-Fallback existiert nicht mehr.

## 1. Environment

Setze diese Variablen vor dem Start der App:

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

## 5. App neu starten

Nach der Migration App/Worker neu starten und prüfen:
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

## Wichtige Hinweise
- Wenn irgendwo noch `DB_BACKEND=sqlite` gesetzt ist, startet die App jetzt absichtlich **nicht mehr**.
- Wenn ein Prozess weiterhin `data/trading.db` benutzt, läuft dort noch alter Code oder ein alter Prozess.
- Datenbank-Passwort rotieren, falls es in Chat/Logs aufgetaucht ist.
- Falls Staging ebenfalls genutzt wird, eigene PostgreSQL-Datenbank/Schema dafür verwenden.
- Die zusätzlichen Indizes zielen vor allem auf Replay- und Simulations-Detailabfragen.
