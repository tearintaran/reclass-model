"""Persistence layer for the variant reclassification engine.

A thin, typed-ish repository over the PostgreSQL schema in ``db/schema.sql``:

  * ``storage.db``              connection + tenant-scoped session helpers (RLS).
  * ``storage.classifications`` clinical classification *receipts*.
  * ``storage.evidence``        de-identified ``research.evidence_events``.
  * ``storage.alerts``          tier-crossing ``clinical.alert`` rows.
  * ``storage.verify``          re-runs the engine to prove a stored receipt
                                reconstructs byte-for-byte (tier + hash).

No ORM: every function takes a psycopg cursor (usually obtained from
``storage.db.tenant_session``) and runs explicit SQL so the trust boundary and
RLS behaviour stay visible.
"""

from storage.db import (  # noqa: F401
    DEFAULT_DB,
    PSYCOPG_AVAILABLE,
    connect,
    ensure_app_role,
    grant_app_role,
    tenant_session,
)
