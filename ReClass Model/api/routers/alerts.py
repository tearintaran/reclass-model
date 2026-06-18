"""Tier-crossing alerts: list and lifecycle transitions.

Alerts only ever exist for tier *crossings* (the reanalysis layer's "only
crossings page" rule), so this surface is small. State transitions are enforced
by the store/lifecycle, not here: an illegal transition is a 409, an unknown
state a 400, an invisible alert a 404.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from ..auth import UserContext
from ..authz import require_permission
from ..deps import get_audit_log, get_store, get_tenant_from_user
from ..schemas import AlertStateRequest
from ..store import ClinicalStore
from ..audit import AuditLog

router = APIRouter(tags=["alerts"])


@router.get("/alerts")
def list_alerts(
    variant_key: Optional[str] = Query(default=None),
    tenant_id: str = Depends(get_tenant_from_user),
    user: UserContext = Depends(require_permission("alert:read")),
    store: ClinicalStore = Depends(get_store),
) -> List[Dict[str, Any]]:
    return store.list_alerts(tenant_id=tenant_id, variant_key=variant_key)


@router.post("/alerts/{alert_id}/state")
def set_alert_state(
    alert_id: str,
    req: AlertStateRequest,
    tenant_id: str = Depends(get_tenant_from_user),
    user: UserContext = Depends(require_permission("alert:write")),
    store: ClinicalStore = Depends(get_store),
    audit: AuditLog = Depends(get_audit_log),
) -> Dict[str, Any]:
    try:
        prior = store.get_alert(tenant_id=tenant_id, alert_id=alert_id)
        updated = store.update_alert_state(
            tenant_id=tenant_id, alert_id=alert_id, state=req.state
        )
    except LookupError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="alert not found")
    except ValueError as exc:
        detail = str(exc)
        code = (status.HTTP_409_CONFLICT if "transition" in detail
                else status.HTTP_400_BAD_REQUEST)
        raise HTTPException(status_code=code, detail=detail)
    audit.append(
        tenant_id=tenant_id,
        actor_id=user.user_id,
        action="alert.state_change",
        resource_type="alert",
        resource_id=alert_id,
        detail={
            "old_state": prior.get("state") if prior else None,
            "new_state": req.state,
        },
    )
    return updated
