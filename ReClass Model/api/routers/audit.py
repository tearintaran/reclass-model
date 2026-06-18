"""Audit log read API (tenant-scoped, permission-gated)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query

from ..audit import AuditLog
from ..auth import UserContext
from ..authz import require_permission
from ..deps import get_audit_log, get_tenant_from_user

router = APIRouter(tags=["audit"])


@router.get("/audit")
def list_audit_entries(
    action: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    tenant_id: str = Depends(get_tenant_from_user),
    user: UserContext = Depends(require_permission("audit:read")),
    audit: AuditLog = Depends(get_audit_log),
) -> List[Dict[str, Any]]:
    return audit.list_entries(tenant_id=tenant_id, action=action, limit=limit)
