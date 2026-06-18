#!/usr/bin/env bash
# Restore a ReClass PostgreSQL database from a plain SQL dump, including dumps
# produced by deploy/backup.sh.
#
# Required:
#   RECLASS_RESTORE_SOURCE=/path/to/reclass_prod_20260101T000000Z.sql.gz
#   RECLASS_RESTORE_TARGET_DB=reclass_restore_test
#
# Optional:
#   RECLASS_MAINTENANCE_DB=postgres
#   RECLASS_RESTORE_DROP=1        # drop/recreate target if it already exists
#
# libpq connection variables (PGHOST, PGPORT, PGUSER, PGPASSWORD) are honored.

set -euo pipefail

SOURCE="${RECLASS_RESTORE_SOURCE:-}"
TARGET_DB="${RECLASS_RESTORE_TARGET_DB:-}"
MAINTENANCE_DB="${RECLASS_MAINTENANCE_DB:-postgres}"
DROP_EXISTING="${RECLASS_RESTORE_DROP:-0}"

usage() {
  sed -n '2,18p' "$0" >&2
}

fail() {
  echo "restore.sh: $*" >&2
  exit 1
}

[[ -n "$SOURCE" ]] || { usage; fail "RECLASS_RESTORE_SOURCE is required"; }
[[ -n "$TARGET_DB" ]] || { usage; fail "RECLASS_RESTORE_TARGET_DB is required"; }
[[ -f "$SOURCE" ]] || fail "source dump does not exist: $SOURCE"
[[ "$TARGET_DB" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || fail "unsafe database name: $TARGET_DB"

command -v psql >/dev/null || fail "psql is required"
command -v createdb >/dev/null || fail "createdb is required"
command -v dropdb >/dev/null || fail "dropdb is required"
if [[ "$SOURCE" == *.gz ]]; then
  command -v gzip >/dev/null || fail "gzip is required for compressed dumps"
fi

if psql -d "$MAINTENANCE_DB" -tAc "SELECT 1 FROM pg_database WHERE datname = '$TARGET_DB'" | grep -qx "1"; then
  if [[ "$DROP_EXISTING" != "1" ]]; then
    fail "target database '$TARGET_DB' already exists; set RECLASS_RESTORE_DROP=1 to replace it"
  fi
  echo "Dropping existing target database '$TARGET_DB'"
  psql -d "$MAINTENANCE_DB" -v ON_ERROR_STOP=1 -c \
    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '$TARGET_DB' AND pid <> pg_backend_pid()"
  dropdb --if-exists "$TARGET_DB"
fi

echo "Creating target database '$TARGET_DB'"
createdb "$TARGET_DB"

echo "Restoring '$SOURCE' into '$TARGET_DB'"
if [[ "$SOURCE" == *.gz ]]; then
  gzip -dc "$SOURCE" | psql -d "$TARGET_DB" -v ON_ERROR_STOP=1
else
  psql -d "$TARGET_DB" -v ON_ERROR_STOP=1 -f "$SOURCE"
fi

echo "Restore complete: '$TARGET_DB'"
