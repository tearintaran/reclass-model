#!/usr/bin/env bash
# Backup the ReClass PostgreSQL clinical database.
#
# Usage:
#   RECLASS_DB=reclass_prod ./deploy/backup.sh
#   RECLASS_DB=reclass_prod RECLASS_BACKUP_DIR=/var/backups/reclass ./deploy/backup.sh
#
# Requires: pg_dump, psql (PostgreSQL client tools)

set -euo pipefail

DB="${RECLASS_DB:-reclass_dev}"
DIR="${RECLASS_BACKUP_DIR:-./backups}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="${DIR}/${DB}_${STAMP}.sql.gz"

mkdir -p "$DIR"

echo "Backing up database ${DB} -> ${OUT}"
pg_dump --format=plain --no-owner --no-acl "$DB" | gzip > "$OUT"

# Retention: keep last 14 daily-style backups (by filename sort). Use portable
# Bash instead of GNU-only `head -n -14` / `xargs -r` flags.
backups=()
while IFS= read -r backup; do
  backups+=("$backup")
done < <(ls -1 "${DIR}/${DB}_"*.sql.gz 2>/dev/null | sort)

if (( ${#backups[@]} > 14 )); then
  delete_count=$(( ${#backups[@]} - 14 ))
  for ((i = 0; i < delete_count; i++)); do
    rm -f "${backups[$i]}"
  done
fi

echo "Done. $(ls -lh "$OUT" | awk '{print $5}') written."
