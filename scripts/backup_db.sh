#!/usr/bin/env bash
# PostgreSQL-Backup für TradePlatform
# Legt täglich ein komprimiertes SQL-Dump an und bereinigt Dumps älter als 14 Tage.
# Voraussetzung: sudo apt-get install postgresql-client

set -euo pipefail

ENV_FILE="$(dirname "$0")/../.env"
if [[ ! -f "$ENV_FILE" ]]; then
    echo "FEHLER: .env nicht gefunden unter $ENV_FILE" >&2
    exit 1
fi

# Einzelne Variablen aus .env lesen (kein source wegen Sonderzeichen)
_get() { grep -E "^$1=" "$ENV_FILE" | head -1 | cut -d= -f2-; }

PGHOST="$(_get POSTGRES_HOST)"
PGPORT="$(_get POSTGRES_PORT)"
PGDATABASE="$(_get POSTGRES_DB)"
PGUSER="$(_get POSTGRES_USER)"
PGPASSWORD="$(_get POSTGRES_PASSWORD)"

export PGPASSWORD

BACKUP_DIR="/home/martin/backups/tradeplatform"
TIMESTAMP=$(date +%F_%H%M)
OUTFILE="$BACKUP_DIR/tp_${TIMESTAMP}.sql.gz"

mkdir -p "$BACKUP_DIR"

echo "[$(date -Iseconds)] Starte Backup → $OUTFILE"
pg_dump -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" "$PGDATABASE" | gzip > "$OUTFILE"
echo "[$(date -Iseconds)] Backup fertig: $(du -sh "$OUTFILE" | cut -f1)"

# Dumps älter als 14 Tage löschen
find "$BACKUP_DIR" -name "tp_*.sql.gz" -mtime +14 -delete
echo "[$(date -Iseconds)] Alte Backups bereinigt. Verbleibend: $(ls "$BACKUP_DIR"/tp_*.sql.gz 2>/dev/null | wc -l) Dateien"
