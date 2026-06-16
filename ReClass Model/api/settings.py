"""Runtime settings for the API (environment-driven, with safe defaults).

Kept intentionally tiny: the API has almost no configuration of its own because
it delegates to the existing engine/storage layers. The two switches that matter
are which PostgreSQL database the real store connects to and whether the
development-only ``/validation/run`` endpoint is reachable.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    """Immutable API settings resolved once at app construction."""

    #: ``development`` | ``staging`` | ``production``. Gates dev-only endpoints.
    environment: str = "development"
    #: PostgreSQL database the real (DB-backed) store connects to.
    db_name: str = "reclass_dev"
    #: Optional non-superuser role to ``SET LOCAL ROLE`` so RLS is enforced even
    #: when the connecting role is a superuser/owner (see ``storage.db``).
    db_role: str | None = None
    #: HTTP header carrying the caller's tenant id (a UUID).
    tenant_header: str = "X-Tenant-Id"

    @property
    def is_development(self) -> bool:
        return self.environment.strip().lower() == "development"


def get_settings() -> Settings:
    """Build :class:`Settings` from the environment (used as a FastAPI default)."""
    return Settings(
        environment=os.environ.get("RECLASS_API_ENV", "development"),
        db_name=os.environ.get("RECLASS_DB", "reclass_dev"),
        db_role=os.environ.get("RECLASS_DB_ROLE") or None,
        tenant_header=os.environ.get("RECLASS_TENANT_HEADER", "X-Tenant-Id"),
    )
