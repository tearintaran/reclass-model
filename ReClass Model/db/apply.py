#!/usr/bin/env python3
"""Idempotent schema apply / migration tool for the reclassification store.

Creates the target database if missing, runs ``db/schema.sql`` against it, and
then applies SQL migrations under ``deploy/migrations`` in filename order. The
schema itself is fully ``IF NOT EXISTS``-guarded, so re-applying is safe; the
``--drop`` flag recreates the database from scratch (used by the integration
tests against a throwaway database).

Migrations are tracked in ``public.reclass_schema_migrations`` by migration id,
filename, and SHA-256 checksum. Re-applying a migration with the same checksum is
skipped; changing an applied migration fails loudly.

Backends, in order of preference:
  * ``psycopg`` (clean session handling) when importable;
  * otherwise the ``psql`` / ``createdb`` / ``dropdb`` CLIs via subprocess.

Connection parameters come from the standard libpq environment variables
(``PGHOST``, ``PGPORT``, ``PGUSER``, ``PGPASSWORD``); on a default local install
nothing needs to be set.

Usage:
    python db/apply.py [reclass_dev] [--drop] [--schema db/schema.sql]
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"
MIGRATIONS_PATH = Path(__file__).resolve().parents[1] / "deploy" / "migrations"
DEFAULT_DB = os.environ.get("RECLASS_DB", "reclass_dev")
# Maintenance database to connect to for CREATE/DROP DATABASE statements.
MAINTENANCE_DB = os.environ.get("RECLASS_MAINTENANCE_DB", "postgres")
LEDGER_TABLE = "public.reclass_schema_migrations"
_MIGRATION_FILENAME_RE = re.compile(r"^\d+_[A-Za-z0-9_]+\.sql$")
_TRANSACTION_CONTROL_RE = re.compile(
    r"^\s*(BEGIN|COMMIT|ROLLBACK)\s*;\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_NON_TRANSACTIONAL_RE = re.compile(
    r"\b(CREATE\s+DATABASE|DROP\s+DATABASE|ALTER\s+SYSTEM|VACUUM|CREATE\s+INDEX\s+CONCURRENTLY|"
    r"REINDEX\s+(DATABASE|SYSTEM))\b",
    re.IGNORECASE,
)

LEDGER_DDL = f"""
CREATE TABLE IF NOT EXISTS {LEDGER_TABLE} (
    migration_id   text PRIMARY KEY,
    filename       text NOT NULL,
    checksum_sha256 text NOT NULL,
    applied_at     timestamptz NOT NULL DEFAULT now(),
    duration_ms    numeric NOT NULL DEFAULT 0,
    status         text NOT NULL DEFAULT 'applied',
    CHECK (status = 'applied'),
    CHECK (checksum_sha256 ~ '^[0-9a-f]{{64}}$')
);
"""

try:  # optional, preferred backend
    import psycopg
    from psycopg import sql as _sql

    PSYCOPG_AVAILABLE = True
except Exception:  # pragma: no cover - import guard
    psycopg = None
    _sql = None
    PSYCOPG_AVAILABLE = False


class MigrationError(RuntimeError):
    """Base class for migration application failures."""


class MigrationChecksumError(MigrationError):
    """Raised when an applied migration's checksum no longer matches the file."""


class MigrationStateError(MigrationError):
    """Raised when a migration cannot be safely applied transactionally."""


@dataclass(frozen=True)
class Migration:
    migration_id: str
    filename: str
    path: Path
    checksum_sha256: str


def migration_checksum(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def discover_migrations(migrations_path: Path = MIGRATIONS_PATH) -> list[Migration]:
    """Return migrations in deterministic filename order."""
    migrations_path = Path(migrations_path)
    if not migrations_path.exists():
        return []
    migrations: list[Migration] = []
    seen: set[str] = set()
    for path in sorted(migrations_path.glob("*.sql"), key=lambda p: p.name):
        if not _MIGRATION_FILENAME_RE.match(path.name):
            raise MigrationStateError(
                f"migration filename must match 001_description.sql: {path.name}"
            )
        migration_id = path.stem
        if migration_id in seen:
            raise MigrationStateError(f"duplicate migration id: {migration_id}")
        seen.add(migration_id)
        migrations.append(
            Migration(
                migration_id=migration_id,
                filename=path.name,
                path=path,
                checksum_sha256=migration_checksum(path),
            )
        )
    return migrations


def _validate_transactional_migration(migration: Migration, ddl: str) -> None:
    """Reject migration SQL that cannot be safely wrapped with the ledger write."""
    if _TRANSACTION_CONTROL_RE.search(ddl):
        raise MigrationStateError(
            f"{migration.filename} contains explicit transaction control; "
            "migrations are wrapped by db/apply.py"
        )
    if _NON_TRANSACTIONAL_RE.search(ddl):
        raise MigrationStateError(
            f"{migration.filename} contains PostgreSQL statements that cannot run "
            "inside the migration transaction"
        )


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


# --------------------------------------------------------------------------- #
# psycopg backend                                                             #
# --------------------------------------------------------------------------- #
def _pg_database_exists(name: str) -> bool:
    with psycopg.connect(dbname=MAINTENANCE_DB, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,))
            return cur.fetchone() is not None


def _pg_drop_database(name: str) -> None:
    with psycopg.connect(dbname=MAINTENANCE_DB, autocommit=True) as conn:
        with conn.cursor() as cur:
            # Terminate other sessions so DROP cannot block on a stray connection.
            cur.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (name,),
            )
            cur.execute(
                _sql.SQL("DROP DATABASE IF EXISTS {}").format(_sql.Identifier(name))
            )


def _pg_create_database(name: str) -> None:
    with psycopg.connect(dbname=MAINTENANCE_DB, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(_sql.SQL("CREATE DATABASE {}").format(_sql.Identifier(name)))


def _pg_apply_schema(name: str, schema_path: Path) -> None:
    ddl = schema_path.read_text(encoding="utf-8")
    with psycopg.connect(dbname=name) as conn:
        # psycopg executes the whole script (BEGIN/COMMIT + DO blocks) as one batch.
        conn.execute(ddl)
        conn.commit()


def _pg_apply_migrations(name: str, migrations_path: Path) -> list[Migration]:
    applied: list[Migration] = []
    with psycopg.connect(dbname=name, autocommit=True) as conn:
        conn.execute(LEDGER_DDL)
        for migration in discover_migrations(migrations_path):
            row = conn.execute(
                f"SELECT checksum_sha256, status FROM {LEDGER_TABLE} WHERE migration_id = %s",
                (migration.migration_id,),
            ).fetchone()
            if row is not None:
                checksum, status = row
                if checksum != migration.checksum_sha256:
                    raise MigrationChecksumError(
                        f"migration checksum mismatch for {migration.migration_id}: "
                        f"database has {checksum}, file has {migration.checksum_sha256}"
                    )
                if status != "applied":
                    raise MigrationStateError(
                        f"migration {migration.migration_id} has unexpected status {status!r}"
                    )
                continue

            ddl = migration.path.read_text(encoding="utf-8")
            _validate_transactional_migration(migration, ddl)
            started = time.monotonic()
            with conn.transaction():
                conn.execute(ddl)
                duration_ms = round((time.monotonic() - started) * 1000, 3)
                conn.execute(
                    f"""
                    INSERT INTO {LEDGER_TABLE}
                        (migration_id, filename, checksum_sha256, duration_ms, status)
                    VALUES (%s, %s, %s, %s, 'applied')
                    """,
                    (
                        migration.migration_id,
                        migration.filename,
                        migration.checksum_sha256,
                        duration_ms,
                    ),
                )
            applied.append(migration)
    return applied


# --------------------------------------------------------------------------- #
# psql CLI backend                                                            #
# --------------------------------------------------------------------------- #
def _cli_database_exists(name: str) -> bool:
    out = subprocess.run(
        [
            "psql",
            "-d",
            MAINTENANCE_DB,
            "-tAc",
            f"SELECT 1 FROM pg_database WHERE datname = {_sql_literal(name)}",
        ],
        capture_output=True, text=True, check=True,
    )
    return out.stdout.strip() == "1"


def _cli_drop_database(name: str) -> None:
    subprocess.run(["dropdb", "--if-exists", name], check=True)


def _cli_create_database(name: str) -> None:
    subprocess.run(["createdb", name], check=True)


def _cli_apply_schema(name: str, schema_path: Path) -> None:
    subprocess.run(
        ["psql", "-d", name, "-v", "ON_ERROR_STOP=1", "-f", str(schema_path)],
        check=True,
    )


def _cli_apply_migrations(name: str, migrations_path: Path) -> list[Migration]:
    applied: list[Migration] = []
    subprocess.run(
        ["psql", "-d", name, "-v", "ON_ERROR_STOP=1", "-c", LEDGER_DDL],
        check=True,
    )
    for migration in discover_migrations(migrations_path):
        out = subprocess.run(
            [
                "psql",
                "-d",
                name,
                "-tA",
                "-F",
                "\t",
                "-c",
                "SELECT checksum_sha256, status "
                f"FROM {LEDGER_TABLE} "
                f"WHERE migration_id = {_sql_literal(migration.migration_id)}",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        row = out.stdout.strip()
        if row:
            checksum, status = row.split("\t", 1)
            if checksum != migration.checksum_sha256:
                raise MigrationChecksumError(
                    f"migration checksum mismatch for {migration.migration_id}: "
                    f"database has {checksum}, file has {migration.checksum_sha256}"
                )
            if status != "applied":
                raise MigrationStateError(
                    f"migration {migration.migration_id} has unexpected status {status!r}"
                )
            continue

        ddl = migration.path.read_text(encoding="utf-8")
        _validate_transactional_migration(migration, ddl)
        wrapper = (
            "BEGIN;\n"
            f"{ddl}\n"
            f"INSERT INTO {LEDGER_TABLE} "
            "(migration_id, filename, checksum_sha256, duration_ms, status) VALUES "
            f"({_sql_literal(migration.migration_id)}, {_sql_literal(migration.filename)}, "
            f"{_sql_literal(migration.checksum_sha256)}, 0, 'applied');\n"
            "COMMIT;\n"
        )
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", suffix=".sql", delete=False
        ) as tmp:
            tmp.write(wrapper)
            tmp_path = tmp.name
        try:
            subprocess.run(
                ["psql", "-d", name, "-v", "ON_ERROR_STOP=1", "-f", tmp_path],
                check=True,
            )
        finally:
            Path(tmp_path).unlink(missing_ok=True)
        applied.append(migration)
    return applied


# --------------------------------------------------------------------------- #
# Backend-agnostic operations                                                 #
# --------------------------------------------------------------------------- #
def database_exists(name: str) -> bool:
    return _pg_database_exists(name) if PSYCOPG_AVAILABLE else _cli_database_exists(name)


def drop_database(name: str) -> None:
    (_pg_drop_database if PSYCOPG_AVAILABLE else _cli_drop_database)(name)


def create_database(name: str) -> None:
    (_pg_create_database if PSYCOPG_AVAILABLE else _cli_create_database)(name)


def apply_schema(name: str, schema_path: Path = SCHEMA_PATH) -> None:
    (_pg_apply_schema if PSYCOPG_AVAILABLE else _cli_apply_schema)(name, Path(schema_path))


def apply_migrations(name: str, migrations_path: Path = MIGRATIONS_PATH) -> list[Migration]:
    return (_pg_apply_migrations if PSYCOPG_AVAILABLE else _cli_apply_migrations)(
        name, Path(migrations_path)
    )


def recreate_database(
    name: str,
    schema_path: Path = SCHEMA_PATH,
    migrations_path: Path = MIGRATIONS_PATH,
    *,
    run_migrations: bool = True,
) -> None:
    """Drop (if present), create, and apply the schema. Used by the test harness."""
    drop_database(name)
    create_database(name)
    apply_schema(name, schema_path)
    if run_migrations:
        apply_migrations(name, migrations_path)


def apply(
    name: str = DEFAULT_DB,
    *,
    drop: bool = False,
    schema_path: Path = SCHEMA_PATH,
    migrations_path: Path = MIGRATIONS_PATH,
    run_migrations: bool = True,
) -> list[Migration]:
    """Idempotently ensure ``name`` exists with the schema applied."""
    schema_path = Path(schema_path)
    if not schema_path.exists():
        raise FileNotFoundError(f"schema file not found: {schema_path}")
    if drop:
        drop_database(name)
    if not database_exists(name):
        create_database(name)
    apply_schema(name, schema_path)
    if run_migrations:
        return apply_migrations(name, migrations_path)
    return []


# --------------------------------------------------------------------------- #
# CLI                                                                         #
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "database", nargs="?", help=f"target database name (default: {DEFAULT_DB})"
    )
    parser.add_argument(
        "--db", default=DEFAULT_DB, help=f"target database name (default: {DEFAULT_DB})"
    )
    parser.add_argument(
        "--drop", action="store_true", help="drop and recreate the database before applying"
    )
    parser.add_argument("--schema", default=str(SCHEMA_PATH), help="path to schema.sql")
    parser.add_argument(
        "--migrations", default=str(MIGRATIONS_PATH), help="path to ordered SQL migrations"
    )
    parser.add_argument(
        "--no-migrations", action="store_true", help="apply only the base schema"
    )
    args = parser.parse_args(argv)

    backend = "psycopg" if PSYCOPG_AVAILABLE else "psql CLI"
    db_name = args.database or args.db
    applied = apply(
        db_name,
        drop=args.drop,
        schema_path=Path(args.schema),
        migrations_path=Path(args.migrations),
        run_migrations=not args.no_migrations,
    )
    action = "recreated" if args.drop else "applied"
    migration_msg = "migrations skipped"
    if not args.no_migrations:
        migration_msg = f"{len(applied)} migration(s) applied"
    print(
        f"[apply] schema {action} on database '{db_name}' via {backend}; "
        f"{migration_msg}."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
