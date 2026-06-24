"""Audit log read API (tenant-scoped, permission-gated)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from ..audit import AuditLog, append_security_event, apply_retention_policy
from ..auth import UserContext
from ..authz import require_permission
from ..deps import get_app_settings, get_audit_log, get_tenant_from_user
from ..settings import Settings

router = APIRouter(tags=["audit"])


class SecurityEventRequest(BaseModel):
    event_type: str = Field(pattern=r"^[a-z0-9_.-]+$")
    outcome: str = Field(pattern=r"^[a-z0-9_.-]+$")
    actor_id: str = "system"
    resource_type: str = "security"
    resource_id: str = "platform"
    detail: Dict[str, Any] = Field(default_factory=dict)


class RetentionRequest(BaseModel):
    retention_days: Optional[int] = Field(default=None, ge=0)


@router.get("/audit")
def list_audit_entries(
    action: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    tenant_id: str = Depends(get_tenant_from_user),
    user: UserContext = Depends(require_permission("audit:read")),
    audit: AuditLog = Depends(get_audit_log),
) -> List[Dict[str, Any]]:
    return audit.list_entries(tenant_id=tenant_id, action=action, limit=limit)


@router.get("/audit/retention")
def audit_retention_policy(
    settings: Settings = Depends(get_app_settings),
    tenant_id: str = Depends(get_tenant_from_user),
    user: UserContext = Depends(require_permission("audit:read")),
) -> Dict[str, Any]:
    return {
        "tenant_id": tenant_id,
        "retention_days": settings.audit_retention_days,
        "backend": settings.audit_backend,
        "memory_max_entries": settings.audit_max_entries,
    }


@router.post("/audit/retention/apply")
def apply_audit_retention(
    req: RetentionRequest,
    settings: Settings = Depends(get_app_settings),
    tenant_id: str = Depends(get_tenant_from_user),
    user: UserContext = Depends(require_permission("audit:write")),
    audit: AuditLog = Depends(get_audit_log),
) -> Dict[str, Any]:
    days = settings.audit_retention_days if req.retention_days is None else req.retention_days
    pruned = apply_retention_policy(audit, tenant_id=tenant_id, retention_days=days)
    return {"tenant_id": tenant_id, "retention_days": days, "pruned": pruned}


@router.post("/audit/security-events", status_code=201)
def create_security_event(
    req: SecurityEventRequest,
    tenant_id: str = Depends(get_tenant_from_user),
    user: UserContext = Depends(require_permission("audit:write")),
    audit: AuditLog = Depends(get_audit_log),
) -> Dict[str, Any]:
    entry = append_security_event(
        audit,
        tenant_id=tenant_id,
        event_type=req.event_type,
        outcome=req.outcome,
        actor_id=req.actor_id or user.user_id,
        resource_type=req.resource_type,
        resource_id=req.resource_id,
        detail=req.detail,
    )
    return entry.to_dict()
