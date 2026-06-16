#!/usr/bin/env python3
"""Idempotent schema apply / migration tool for the reclassification store.

Creates the target database if missing and runs ``db/schema.sql`` against it. The
schema itself is fully ``IF NOT EXISTS``-guarded, so re-applying is safe; the
``--drop`` flag recreates the database from scratch (used by the integration
tests against a throwaway database).

Backends, in order of preference:
  * ``psycopg`` (clean session handling) when importable;
  * otherwise the ``psql`` / ``createdb`` / ``dropdb`` CLIs via subprocess.

Connection parameters come from the standard libpq environment variables
(``PGHOST``, ``PGPORT``, ``PGUSER``, ``PGPASSWORD``); on a default local install
nothing needs to be set.

Usage:
    python db/apply.py [--db reclass_dev] [--drop] [--schema db/schema.sql]
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"
DEFAULT_DB = os.environ.get("RECLASS_DB", "reclass_dev")
# Maintenance database to connect to for CREATE/DROP DATABASE statements.
MAINTENANCE_DB = os.environ.get("RECLASS_MAINTENANCE_DB", "postgres")

try:  # optional, preferred backend
    import psycopg
    from psycopg import sql as _sql

    PSYCOPG_AVAILABLE = True
except Exception:  # pragma: no cover - import guard
    psycopg = None
    _sql = None
    PSYCOPG_AVAILABLE = False


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


# --------------------------------------------------------------------------- #
# psql CLI backend                                                            #
# --------------------------------------------------------------------------- #
def _cli_database_exists(name: str) -> bool:
    out = subprocess.run(
        ["psql", "-d", MAINTENANCE_DB, "-tAc",
         f"SELECT 1 FROM pg_database WHERE datname = '{name}'"],
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


def recreate_database(name: str, schema_path: Path = SCHEMA_PATH) -> None:
    """Drop (if present), create, and apply the schema. Used by the test harness."""
    drop_database(name)
    create_database(name)
    apply_schema(name, schema_path)


def apply(name: str = DEFAULT_DB, *, drop: bool = False,
          schema_path: Path = SCHEMA_PATH) -> None:
    """Idempotently ensure ``name`` exists with the schema applied."""
    schema_path = Path(schema_path)
    if not schema_path.exists():
        raise FileNotFoundError(f"schema file not found: {schema_path}")
    if drop:
        drop_database(name)
    if not database_exists(name):
        create_database(name)
    apply_schema(name, schema_path)


# --------------------------------------------------------------------------- #
# CLI                                                                         #
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=DEFAULT_DB,
                        help=f"target database name (default: {DEFAULT_DB})")
    parser.add_argument("--drop", action="store_true",
                        help="drop and recreate the database before applying")
    parser.add_argument("--schema", default=str(SCHEMA_PATH),
                        help="path to schema.sql")
    args = parser.parse_args(argv)

    backend = "psycopg" if PSYCOPG_AVAILABLE else "psql CLI"
    apply(args.db, drop=args.drop, schema_path=Path(args.schema))
    action = "recreated" if args.drop else "applied"
    print(f"[apply] schema {action} on database '{args.db}' via {backend}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
