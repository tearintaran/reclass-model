"""Tenant administration and onboarding readiness endpoints."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from ops.onboarding import preproduction_readiness_report
from storage.admin import TenantAdminStore

from ..auth import UserContext
from ..authz import require_permission
from ..deps import get_app_settings
from ..settings import Settings

router = APIRouter(prefix="/admin", tags=["admin"])


class TenantCreateRequest(BaseModel):
    name: str = Field(min_length=1)
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,62}$")
    contact_email: Optional[str] = None
    oidc_issuer: Optional[str] = None
    oidc_audience: Optional[str] = None
    status: str = "onboarding"


class TenantUpdateRequest(BaseModel):
    name: Optional[str] = None
    slug: Optional[str] = Field(default=None, pattern=r"^[a-z0-9][a-z0-9-]{1,62}$")
    contact_email: Optional[str] = None
    oidc_issuer: Optional[str] = None
    oidc_audience: Optional[str] = None
    status: Optional[str] = None


def get_admin_store(request: Request) -> TenantAdminStore:
    store = getattr(request.app.state, "admin_store", None)
    if store is None:  # pragma: no cover
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="tenant admin store not configured",
        )
    return store


@router.post("/tenants", status_code=201)
def create_tenant(
    req: TenantCreateRequest,
    user: UserContext = Depends(require_permission("tenant:admin")),
    store: TenantAdminStore = Depends(get_admin_store),
) -> Dict[str, Any]:
    try:
        return store.create_tenant(**req.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))


@router.get("/tenants")
def list_tenants(
    user: UserContext = Depends(require_permission("tenant:admin")),
    store: TenantAdminStore = Depends(get_admin_store),
) -> List[Dict[str, Any]]:
    return store.list_tenants()


@router.get("/tenants/{tenant_id}")
def get_tenant(
    tenant_id: str,
    user: UserContext = Depends(require_permission("tenant:admin")),
    store: TenantAdminStore = Depends(get_admin_store),
) -> Dict[str, Any]:
    tenant = store.get_tenant(tenant_id)
    if tenant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="tenant not found")
    return tenant


@router.patch("/tenants/{tenant_id}")
def update_tenant(
    tenant_id: str,
    req: TenantUpdateRequest,
    user: UserContext = Depends(require_permission("tenant:admin")),
    store: TenantAdminStore = Depends(get_admin_store),
) -> Dict[str, Any]:
    try:
        return store.update_tenant(
            tenant_id,
            req.model_dump(exclude_none=True),
        )
    except LookupError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="tenant not found")
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))


@router.get("/tenants/{tenant_id}/readiness")
def tenant_readiness(
    tenant_id: str,
    request: Request,
    settings: Settings = Depends(get_app_settings),
    user: UserContext = Depends(require_permission("tenant:admin")),
    store: TenantAdminStore = Depends(get_admin_store),
) -> Dict[str, Any]:
    tenant = store.get_tenant(tenant_id)
    if tenant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="tenant not found")
    base_path = Path(request.app.state.base_path)
    return preproduction_readiness_report(settings, tenant=tenant, base_path=base_path)
