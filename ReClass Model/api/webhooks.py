"""Webhook event emission, signing, and delivery runner."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from datetime import datetime
from typing import Any, Callable, Dict, Optional, Tuple

from storage.webhooks import WebhookStore, next_retry_at

WEBHOOK_EVENT_TYPES = (
    "tier_crossing",
    "source_snapshot_update",
    "config_change",
    "reanalysis_completed",
)


def canonical_payload(payload: Dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sign_payload(secret: str, body: bytes, *, timestamp: Optional[int] = None) -> str:
    ts = int(time.time() if timestamp is None else timestamp)
    signed = f"{ts}.".encode("ascii") + body
    digest = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()
    return f"t={ts},v1={digest}"


def verify_signature(secret: str, body: bytes, signature: str, *, tolerance_seconds: int = 300) -> bool:
    parts = dict(part.split("=", 1) for part in signature.split(",") if "=" in part)
    try:
        timestamp = int(parts["t"])
    except (KeyError, ValueError):
        return False
    if abs(int(time.time()) - timestamp) > tolerance_seconds:
        return False
    expected = sign_payload(secret, body, timestamp=timestamp)
    return hmac.compare_digest(expected, signature)


def emit_event(
    store: WebhookStore,
    *,
    tenant_id: str,
    event_type: str,
    payload: Dict[str, Any],
    source_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Public lifecycle seam for other modules to enqueue outbound events."""
    if event_type not in WEBHOOK_EVENT_TYPES:
        raise ValueError(f"unsupported webhook event type: {event_type}")
    envelope = {
        "event_type": event_type,
        "tenant_id": tenant_id,
        "source_id": source_id,
        "payload": payload,
    }
    return store.create_event(
        tenant_id=tenant_id,
        event_type=event_type,
        payload=envelope,
        source_id=source_id,
    )


Sender = Callable[[str, Dict[str, str], bytes], Tuple[int, str]]


def deliver_due(
    store: WebhookStore,
    *,
    sender: Sender,
    limit: int = 100,
    max_attempts: int = 5,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Deliver due webhook jobs using an injected sender.

    ``sender`` is injected so tests stay offline and production can wrap httpx,
    requests, or a queue worker without changing the store or event seam.
    """
    delivered = 0
    retrying = 0
    failed = 0
    for delivery in store.due_deliveries(limit=limit, now=now):
        body = canonical_payload(delivery["payload"])
        headers = {
            "Content-Type": "application/json",
            "X-ReClass-Event": delivery["event_type"],
            "X-ReClass-Delivery": delivery["delivery_id"],
            "X-ReClass-Signature": sign_payload(delivery["secret"], body),
        }
        try:
            status_code, response_body = sender(delivery["url"], headers, body)
        except Exception as exc:
            status_code, response_body = None, str(exc)
        attempts_after = int(delivery.get("attempts") or 0) + 1
        if status_code is not None and 200 <= status_code < 300:
            store.mark_delivery_attempt(
                delivery_id=delivery["delivery_id"],
                state="delivered",
                status_code=status_code,
                response_body=response_body,
                next_attempt_at=None,
            )
            delivered += 1
        elif attempts_after >= max_attempts:
            store.mark_delivery_attempt(
                delivery_id=delivery["delivery_id"],
                state="failed",
                status_code=status_code,
                response_body=response_body,
                next_attempt_at=None,
            )
            failed += 1
        else:
            store.mark_delivery_attempt(
                delivery_id=delivery["delivery_id"],
                state="retry",
                status_code=status_code,
                response_body=response_body,
                next_attempt_at=next_retry_at(attempts_after, now=now),
            )
            retrying += 1
    return {"delivered": delivered, "retrying": retrying, "failed": failed}
