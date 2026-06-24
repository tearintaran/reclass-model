"""Webhook endpoint registration and outbound event queue API."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from storage.webhooks import WebhookStore

from ..auth import UserContext
from ..authz import require_permission
from ..webhooks import WEBHOOK_EVENT_TYPES, emit_event

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


class WebhookEndpointRequest(BaseModel):
    url: str = Field(pattern=r"^https?://")
    secret: str = Field(min_length=16)
    event_types: List[str]
    description: str = ""
    enabled: bool = True


class WebhookEndpointUpdateRequest(BaseModel):
    url: Optional[str] = Field(default=None, pattern=r"^https?://")
    secret: Optional[str] = Field(default=None, min_length=16)
    event_types: Optional[List[str]] = None
    description: Optional[str] = None
    enabled: Optional[bool] = None


class WebhookEventRequest(BaseModel):
    event_type: str
    payload: Dict[str, Any] = Field(default_factory=dict)
    source_id: Optional[str] = None


def get_webhook_store(request: Request) -> WebhookStore:
    store = getattr(request.app.state, "webhook_store", None)
    if store is None:  # pragma: no cover
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="webhook store not configured",
        )
    return store


def _tenant(user: UserContext) -> str:
    return user.tenant_id


def _redact(row: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(row)
    if "secret" in out:
        out["secret"] = "***"
    return out


def _validate_event_types(event_types: List[str]) -> None:
    unknown = sorted(set(event_types) - set(WEBHOOK_EVENT_TYPES))
    if unknown:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"unsupported webhook event types: {', '.join(unknown)}",
        )


@router.get("/event-types")
def list_webhook_event_types(
    user: UserContext = Depends(require_permission("webhook:admin")),
) -> Dict[str, Any]:
    return {"event_types": list(WEBHOOK_EVENT_TYPES)}


@router.post("/endpoints", status_code=201)
def register_webhook_endpoint(
    req: WebhookEndpointRequest,
    user: UserContext = Depends(require_permission("webhook:admin")),
    store: WebhookStore = Depends(get_webhook_store),
) -> Dict[str, Any]:
    _validate_event_types(req.event_types)
    row = store.register_endpoint(tenant_id=_tenant(user), **req.model_dump())
    return _redact(row)


@router.get("/endpoints")
def list_webhook_endpoints(
    user: UserContext = Depends(require_permission("webhook:admin")),
    store: WebhookStore = Depends(get_webhook_store),
) -> List[Dict[str, Any]]:
    return [_redact(row) for row in store.list_endpoints(tenant_id=_tenant(user))]


@router.patch("/endpoints/{endpoint_id}")
def update_webhook_endpoint(
    endpoint_id: str,
    req: WebhookEndpointUpdateRequest,
    user: UserContext = Depends(require_permission("webhook:admin")),
    store: WebhookStore = Depends(get_webhook_store),
) -> Dict[str, Any]:
    updates = req.model_dump(exclude_none=True)
    if "event_types" in updates:
        _validate_event_types(updates["event_types"])
    try:
        return _redact(store.update_endpoint(
            tenant_id=_tenant(user),
            endpoint_id=endpoint_id,
            updates=updates,
        ))
    except LookupError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="webhook endpoint not found")


@router.post("/events", status_code=202)
def emit_webhook_event(
    req: WebhookEventRequest,
    user: UserContext = Depends(require_permission("webhook:emit")),
    store: WebhookStore = Depends(get_webhook_store),
) -> Dict[str, Any]:
    try:
        return emit_event(
            store,
            tenant_id=_tenant(user),
            event_type=req.event_type,
            payload=req.payload,
            source_id=req.source_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.get("/events")
def list_webhook_events(
    limit: int = Query(default=100, ge=1, le=1000),
    user: UserContext = Depends(require_permission("webhook:admin")),
    store: WebhookStore = Depends(get_webhook_store),
) -> List[Dict[str, Any]]:
    return store.list_events(tenant_id=_tenant(user), limit=limit)


@router.get("/deliveries")
def list_webhook_deliveries(
    state: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    user: UserContext = Depends(require_permission("webhook:admin")),
    store: WebhookStore = Depends(get_webhook_store),
) -> List[Dict[str, Any]]:
    return [
        _redact(row)
        for row in store.list_deliveries(tenant_id=_tenant(user), state=state, limit=limit)
    ]
