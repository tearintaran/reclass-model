"""Connection and tenant-scoped session helpers.

Row-level security in ``db/schema.sql`` is keyed on the GUC
``app.current_tenant``: each clinical policy is ``USING (tenant_id =
current_setting('app.current_tenant', true)::uuid)``. Two things matter for this
to actually isolate tenants:

1. The tenant GUC must be set for the session/transaction issuing the query.
   We use ``set_config('app.current_tenant', <uuid>, is_local => true)`` so it is
   scoped to the surrounding transaction and reset automatically afterwards.

2. PostgreSQL **bypasses RLS for superusers and for roles with BYPASSRLS**, and —
   *unless the table is FORCEd* — for the table owner too. Every tenant table is now
   ``FORCE ROW LEVEL SECURITY`` (``db/schema.sql`` + ``deploy/migrations/007``), so the
   owner is also subject to the policies; isolation no longer depends on the
   ``SET LOCAL ROLE`` target happening to be a non-owner. ``tenant_session`` still
   ``SET LOCAL ROLE``s to a non-superuser, non-BYPASSRLS role (``ensure_app_role`` /
   ``grant_app_role``) for per-request work, and production preflight rejects a
   ``RECLASS_DB_ROLE`` that is a superuser or has BYPASSRLS. The remaining bypass
   (superuser / BYPASSRLS) is the deliberate path for cross-tenant background workers
   such as webhook delivery, whose connection must hold that privilege while
   per-request handlers stay confined.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator, Optional

try:
    import psycopg
    from psycopg import sql
    from psycopg.rows import dict_row

    PSYCOPG_AVAILABLE = True
except Exception:  # pragma: no cover - import guard
    psycopg = None
    sql = None
    dict_row = None
    PSYCOPG_AVAILABLE = False

DEFAULT_DB = os.environ.get("RECLASS_DB", "reclass_dev")


def _require_psycopg() -> None:
    if not PSYCOPG_AVAILABLE:
        raise RuntimeError(
            "psycopg is not installed; install it with `pip install \"psycopg[binary]\"`"
        )


def connect(dbname: str = DEFAULT_DB, *, autocommit: bool = False, **overrides):
    """Open a psycopg connection (dict rows by default).

    Connection parameters default to the libpq environment; ``overrides`` are
    passed straight through to ``psycopg.connect`` (e.g. ``host=``, ``user=``).
    """
    _require_psycopg()
    conninfo = {"dbname": dbname, "row_factory": dict_row, "autocommit": autocommit}
    conninfo.update(overrides)
    return psycopg.connect(**conninfo)


@contextmanager
def tenant_session(conn, tenant_id, *, role: Optional[str] = None) -> Iterator:
    """Yield a cursor inside a transaction scoped to ``tenant_id``.

    The tenant GUC is set transaction-locally so RLS applies to every statement
    run on the yielded cursor; it is cleared when the transaction ends. Passing
    ``role`` additionally switches to a non-privileged role (via ``SET LOCAL
    ROLE``) so RLS is enforced even when the underlying connection is a
    superuser/owner. The transaction commits on clean exit and rolls back on
    error.
    """
    _require_psycopg()
    with conn.transaction():
        with conn.cursor() as cur:
            if role is not None:
                cur.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(role)))
            # set_config with is_local => true ties the GUC to this transaction.
            cur.execute(
                "SELECT set_config('app.current_tenant', %s, true)", (str(tenant_id),)
            )
            yield cur


def ensure_app_role(conn, role: str) -> None:
    """Create a non-superuser, RLS-subject role (idempotent).

    The role is ``NOLOGIN``: it is only ever reached through ``SET LOCAL ROLE``
    from a privileged connection, so no password/auth wiring is needed. Must be
    run on a connection whose role may create roles (e.g. a superuser).
    """
    _require_psycopg()
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,))
        if cur.fetchone() is None:
            cur.execute(
                sql.SQL(
                    "CREATE ROLE {} NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE"
                ).format(sql.Identifier(role))
            )
    conn.commit()


def grant_app_role(conn, role: str) -> None:
    """Grant the app role the schema/table privileges it needs (idempotent)."""
    _require_psycopg()
    ident = sql.Identifier(role)
    stmts = [
        sql.SQL("GRANT USAGE ON SCHEMA clinical, research TO {}").format(ident),
        sql.SQL(
            "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA clinical TO {}"
        ).format(ident),
        sql.SQL(
            "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA research TO {}"
        ).format(ident),
        sql.SQL(
            "GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA clinical TO {}"
        ).format(ident),
        sql.SQL(
            "GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA research TO {}"
        ).format(ident),
    ]
    with conn.cursor() as cur:
        for stmt in stmts:
            cur.execute(stmt)
    conn.commit()


def drop_app_role(conn, role: str) -> None:
    """Best-effort teardown for a role created by :func:`ensure_app_role`."""
    _require_psycopg()
    ident = sql.Identifier(role)
    with conn.cursor() as cur:
        for schema in ("clinical", "research"):
            cur.execute(
                sql.SQL("REVOKE ALL ON ALL TABLES IN SCHEMA {} FROM {}").format(
                    sql.Identifier(schema), ident
                )
            )
            cur.execute(
                sql.SQL("REVOKE ALL ON ALL SEQUENCES IN SCHEMA {} FROM {}").format(
                    sql.Identifier(schema), ident
                )
            )
            cur.execute(
                sql.SQL("REVOKE ALL ON SCHEMA {} FROM {}").format(
                    sql.Identifier(schema), ident
                )
            )
        cur.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(ident))
    conn.commit()
