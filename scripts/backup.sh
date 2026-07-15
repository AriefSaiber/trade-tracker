#!/usr/bin/env bash
# Nightly Postgres dump to the local backup volume (MVP §13).
set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-./backups}"
STAMP="$(date +%Y%m%d_%H%M%S)"
mkdir -p "$BACKUP_DIR"

docker compose -f docker/docker-compose.yml exec -T postgres \
  pg_dump -U "${POSTGRES_USER:-algotrader}" "${POSTGRES_DB:-algotrader}" \
  | gzip > "$BACKUP_DIR/algotrader_$STAMP.sql.gz"

# keep 14 days
find "$BACKUP_DIR" -name 'algotrader_*.sql.gz' -mtime +14 -delete
echo "backup written: $BACKUP_DIR/algotrader_$STAMP.sql.gz"
