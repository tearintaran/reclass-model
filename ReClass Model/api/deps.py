"""FastAPI dependencies: settings, the clinical store, the evidence resolver,
and the tenant identity extracted from the request.

The store and resolver are held on ``app.state`` so tests can construct an app
with an injected :class:`~api.store.InMemoryClinicalStore` and a resolver full of
deterministic fake providers — no database, no network.
"""

from __future__ import annotations

import uuid

from fastapi import Depends, Header, HTTPException, Request, status

from .evidence_resolver import EvidenceResolver
from .settings import Settings, get_settings
from .store import ClinicalStore


def get_app_settings(request: Request) -> Settings:
    return getattr(request.app.state, "settings", None) or get_settings()


def get_store(request: Request) -> ClinicalStore:
    store = getattr(request.app.state, "store", None)
    if store is None:  # pragma: no cover - defensive; app always sets this
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="clinical store not configured",
        )
    return store


def get_resolver(request: Request) -> EvidenceResolver:
    resolver = getattr(request.app.state, "resolver", None)
    if resolver is None:  # pragma: no cover - defensive; app always sets this
        return EvidenceResolver()
    return resolver


def get_tenant_id(
    request: Request,
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
    settings: Settings = Depends(get_app_settings),
) -> str:
    """Resolve and validate the caller's tenant id from the configured header.

    Clinical endpoints depend on this so every clinical query is scoped to a
    tenant. The id must be a UUID (matching the RLS GUC type ``uuid``); a missing
    or malformed tenant is rejected before any clinical data is touched.
    """
    value = x_tenant_id
    if value is None and settings.tenant_header != "X-Tenant-Id":
        value = request.headers.get(settings.tenant_header)
    if not value:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"missing tenant header {settings.tenant_header!r}",
        )
    try:
        uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="tenant id must be a UUID",
        )
    return str(value)
