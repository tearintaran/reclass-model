"""FastAPI dependencies: settings, store, resolver, auth, and audit."""

from __future__ import annotations

import json
import uuid

from fastapi import Depends, Header, HTTPException, Request, status

from .audit import AuditLog, InMemoryAuditLog
from .auth import (
    TenantBindingLookup,
    UserContext,
    authenticate_bearer,
    legacy_user_from_tenant,
)
from .evidence_resolver import EvidenceResolver
from .settings import Settings, get_settings
from .store import ClinicalStore


def get_app_settings(request: Request) -> Settings:
    return getattr(request.app.state, "settings", None) or get_settings()


def get_store(request: Request) -> ClinicalStore:
    store = getattr(request.app.state, "store", None)
    if store is None:  # pragma: no cover
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="clinical store not configured",
        )
    return store


def get_resolver(request: Request) -> EvidenceResolver:
    resolver = getattr(request.app.state, "resolver", None)
    if resolver is None:  # pragma: no cover
        return EvidenceResolver()
    return resolver


def get_audit_log(request: Request) -> AuditLog:
    audit = getattr(request.app.state, "audit_log", None)
    if audit is None:
        audit = InMemoryAuditLog()
        request.app.state.audit_log = audit
    return audit


def get_tenant_id(
    request: Request,
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
    settings: Settings = Depends(get_app_settings),
) -> str:
    """Resolve and validate tenant id from the configured header (legacy)."""
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


def _validate_tenant_uuid(tenant_id: str) -> str:
    try:
        uuid.UUID(str(tenant_id))
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="tenant id must be a UUID",
        )
    return str(tenant_id)


def _tenant_oidc_binding(request: Request) -> TenantBindingLookup:
    """A per-tenant OIDC binding lookup backed by the app's tenant registry.

    Returns a callable that maps ``tenant_id`` to its registered OIDC config. When no
    registry is wired, the callable returns ``None`` for every tenant, so a federated
    token cannot bind to any tenant (fail closed) rather than being trusted blindly.
    """
    store = getattr(request.app.state, "admin_store", None)
    get_tenant = getattr(store, "get_tenant", None)
    if get_tenant is None:
        return lambda tenant_id: None
    return lambda tenant_id: get_tenant(tenant_id)


def get_current_user(
    request: Request,
    authorization: str | None = Header(default=None),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
    settings: Settings = Depends(get_app_settings),
) -> UserContext:
    """Authenticate the caller and return a tenant-scoped user context."""
    bearer: str | None = None
    if authorization and authorization.lower().startswith("bearer "):
        bearer = authorization[7:].strip()

    if bearer:
        user = authenticate_bearer(
            bearer, settings, tenant_binding=_tenant_oidc_binding(request)
        )
        _validate_tenant_uuid(user.tenant_id)
        if x_tenant_id and str(x_tenant_id) != user.tenant_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="X-Tenant-Id does not match authenticated tenant",
            )
        return user

    if settings.allows_legacy_tenant_header():
        if not x_tenant_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="authentication required: provide Authorization Bearer token "
                       f"or {settings.tenant_header!r} in development",
            )
        tenant_id = _validate_tenant_uuid(x_tenant_id)
        return legacy_user_from_tenant(tenant_id, settings)

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="authentication required: Authorization Bearer token",
    )


def get_tenant_from_user(user: UserContext = Depends(get_current_user)) -> str:
    return user.tenant_id


def parse_api_keys(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"RECLASS_API_KEYS is not valid JSON: {exc}") from exc
    return data if isinstance(data, dict) else {}
