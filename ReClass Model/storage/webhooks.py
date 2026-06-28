"""Webhook endpoint, event, and delivery persistence."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Protocol


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _jsonable(row: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(row)
    for key, value in list(out.items()):
        if isinstance(value, uuid.UUID):
            out[key] = str(value)
        elif isinstance(value, datetime):
            out[key] = value.isoformat()
    return out


class WebhookStore(Protocol):
    def register_endpoint(
        self,
        *,
        tenant_id: str,
        url: str,
        secret: str,
        event_types: List[str],
        description: str = "",
        enabled: bool = True,
    ) -> Dict[str, Any]: ...

    def list_endpoints(self, *, tenant_id: str) -> List[Dict[str, Any]]: ...

    def get_endpoint(self, *, tenant_id: str, endpoint_id: str) -> Optional[Dict[str, Any]]: ...

    def update_endpoint(self, *, tenant_id: str, endpoint_id: str, updates: Dict[str, Any]) -> Dict[str, Any]: ...

    def create_event(
        self,
        *,
        tenant_id: str,
        event_type: str,
        payload: Dict[str, Any],
        source_id: Optional[str] = None,
    ) -> Dict[str, Any]: ...

    def list_events(self, *, tenant_id: str, limit: int = 100) -> List[Dict[str, Any]]: ...

    def list_deliveries(
        self, *, tenant_id: str, state: Optional[str] = None, limit: int = 100
    ) -> List[Dict[str, Any]]: ...

    def due_deliveries(self, *, limit: int = 100, now: Optional[datetime] = None) -> List[Dict[str, Any]]: ...

    def mark_delivery_attempt(
        self,
        *,
        delivery_id: str,
        state: str,
        status_code: Optional[int],
        response_body: str,
        next_attempt_at: Optional[datetime],
    ) -> Dict[str, Any]: ...


class InMemoryWebhookStore:
    """In-memory webhook store used by tests and no-DB deployments."""

    def __init__(self) -> None:
        self._endpoints: Dict[str, List[Dict[str, Any]]] = {}
        self._events: Dict[str, List[Dict[str, Any]]] = {}
        self._deliveries: Dict[str, List[Dict[str, Any]]] = {}

    def _tenant_rows(self, table: Dict[str, List[Dict[str, Any]]], tenant_id: str) -> List[Dict[str, Any]]:
        return table.setdefault(str(tenant_id), [])

    def register_endpoint(
        self,
        *,
        tenant_id: str,
        url: str,
        secret: str,
        event_types: List[str],
        description: str = "",
        enabled: bool = True,
    ) -> Dict[str, Any]:
        row = {
            "endpoint_id": str(uuid.uuid4()),
            "tenant_id": tenant_id,
            "url": url,
            "secret": secret,
            "event_types": list(event_types),
            "description": description,
            "enabled": enabled,
            "created_at": _now(),
            "updated_at": _now(),
        }
        self._tenant_rows(self._endpoints, tenant_id).append(row)
        return _jsonable(row)

    def list_endpoints(self, *, tenant_id: str) -> List[Dict[str, Any]]:
        return [_jsonable(row) for row in self._tenant_rows(self._endpoints, tenant_id)]

    def get_endpoint(self, *, tenant_id: str, endpoint_id: str) -> Optional[Dict[str, Any]]:
        for row in self._tenant_rows(self._endpoints, tenant_id):
            if row["endpoint_id"] == endpoint_id:
                return _jsonable(row)
        return None

    def update_endpoint(self, *, tenant_id: str, endpoint_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
        for row in self._tenant_rows(self._endpoints, tenant_id):
            if row["endpoint_id"] == endpoint_id:
                for key in ("url", "secret", "event_types", "description", "enabled"):
                    if key in updates:
                        row[key] = updates[key]
                row["updated_at"] = _now()
                return _jsonable(row)
        raise LookupError(f"webhook endpoint not found: {endpoint_id}")

    def create_event(
        self,
        *,
        tenant_id: str,
        event_type: str,
        payload: Dict[str, Any],
        source_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        event = {
            "event_id": str(uuid.uuid4()),
            "tenant_id": tenant_id,
            "event_type": event_type,
            "source_id": source_id,
            "payload": dict(payload),
            "created_at": _now(),
        }
        self._tenant_rows(self._events, tenant_id).append(event)
        for endpoint in self._tenant_rows(self._endpoints, tenant_id):
            if endpoint.get("enabled") and event_type in endpoint.get("event_types", []):
                self._tenant_rows(self._deliveries, tenant_id).append({
                    "delivery_id": str(uuid.uuid4()),
                    "tenant_id": tenant_id,
                    "event_id": event["event_id"],
                    "endpoint_id": endpoint["endpoint_id"],
                    "event_type": event_type,
                    "url": endpoint["url"],
                    "secret": endpoint["secret"],
                    "payload": dict(payload),
                    "state": "pending",
                    "attempts": 0,
                    "last_status_code": None,
                    "last_response_body": None,
                    "next_attempt_at": _now(),
                    "created_at": _now(),
                    "delivered_at": None,
                })
        return _jsonable(event)

    def list_events(self, *, tenant_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        return [_jsonable(row) for row in self._tenant_rows(self._events, tenant_id)[-limit:]][::-1]

    def list_deliveries(self, *, tenant_id: str, state: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
        rows = self._tenant_rows(self._deliveries, tenant_id)
        if state is not None:
            rows = [row for row in rows if row["state"] == state]
        return [_jsonable(row) for row in rows[-limit:]][::-1]

    def due_deliveries(self, *, limit: int = 100, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
        clock = now or _now()
        rows = []
        for tenant_rows in self._deliveries.values():
            for row in tenant_rows:
                due_at = row.get("next_attempt_at")
                if row["state"] in {"pending", "retry"} and (due_at is None or due_at <= clock):
                    rows.append(row)
        rows.sort(key=lambda row: row["created_at"])
        return [_jsonable(row) for row in rows[:limit]]

    def mark_delivery_attempt(
        self,
        *,
        delivery_id: str,
        state: str,
        status_code: Optional[int],
        response_body: str,
        next_attempt_at: Optional[datetime],
    ) -> Dict[str, Any]:
        for tenant_rows in self._deliveries.values():
            for row in tenant_rows:
                if row["delivery_id"] == delivery_id:
                    row["state"] = state
                    row["attempts"] += 1
                    row["last_status_code"] = status_code
                    row["last_response_body"] = response_body[:2000]
                    row["next_attempt_at"] = next_attempt_at
                    if state == "delivered":
                        row["delivered_at"] = _now()
                    return _jsonable(row)
        raise LookupError(f"webhook delivery not found: {delivery_id}")


class DbWebhookStore:
    """PostgreSQL-backed webhook store."""

    def __init__(self, *, db_name: str = "reclass_dev", role: Optional[str] = None, connect=None) -> None:
        self._db_name = db_name
        self._role = role
        self._connect = connect

    def _conn(self):
        if self._connect is not None:
            return self._connect()
        from storage.db import connect

        return connect(self._db_name)

    def _session(self, conn, tenant_id: str):
        from storage.db import tenant_session

        return tenant_session(conn, tenant_id, role=self._role)

    def register_endpoint(
        self,
        *,
        tenant_id: str,
        url: str,
        secret: str,
        event_types: List[str],
        description: str = "",
        enabled: bool = True,
    ) -> Dict[str, Any]:
        from psycopg.types.json import Jsonb

        with self._conn() as conn:
            with self._session(conn, tenant_id) as cur:
                cur.execute(
                    """
                    INSERT INTO clinical.webhook_endpoint
                        (tenant_id, url, secret, event_types, description, enabled)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    RETURNING *
                    """,
                    (tenant_id, url, secret, Jsonb(event_types), description, enabled),
                )
                return _jsonable(cur.fetchone())

    def list_endpoints(self, *, tenant_id: str) -> List[Dict[str, Any]]:
        with self._conn() as conn:
            with self._session(conn, tenant_id) as cur:
                cur.execute("SELECT * FROM clinical.webhook_endpoint ORDER BY created_at DESC")
                return [_jsonable(row) for row in cur.fetchall()]

    def get_endpoint(self, *, tenant_id: str, endpoint_id: str) -> Optional[Dict[str, Any]]:
        with self._conn() as conn:
            with self._session(conn, tenant_id) as cur:
                cur.execute(
                    "SELECT * FROM clinical.webhook_endpoint WHERE endpoint_id = %s",
                    (endpoint_id,),
                )
                row = cur.fetchone()
                return _jsonable(row) if row else None

    def update_endpoint(self, *, tenant_id: str, endpoint_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
        from psycopg.types.json import Jsonb

        allowed = {"url", "secret", "event_types", "description", "enabled"}
        pairs = [(key, value) for key, value in updates.items() if key in allowed]
        if not pairs:
            row = self.get_endpoint(tenant_id=tenant_id, endpoint_id=endpoint_id)
            if row is None:
                raise LookupError(f"webhook endpoint not found: {endpoint_id}")
            return row
        set_sql = ", ".join(f"{key} = %s" for key, _ in pairs)
        params = [Jsonb(value) if key == "event_types" else value for key, value in pairs]
        params.append(endpoint_id)
        with self._conn() as conn:
            with self._session(conn, tenant_id) as cur:
                cur.execute(
                    f"""
                    UPDATE clinical.webhook_endpoint
                       SET {set_sql}, updated_at = now()
                     WHERE endpoint_id = %s
                    RETURNING *
                    """,
                    params,
                )
                row = cur.fetchone()
                if row is None:
                    raise LookupError(f"webhook endpoint not found: {endpoint_id}")
                return _jsonable(row)

    def create_event(
        self,
        *,
        tenant_id: str,
        event_type: str,
        payload: Dict[str, Any],
        source_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        from psycopg.types.json import Jsonb

        with self._conn() as conn:
            with self._session(conn, tenant_id) as cur:
                cur.execute(
                    """
                    INSERT INTO clinical.webhook_event
                        (tenant_id, event_type, source_id, payload)
                    VALUES (%s, %s, %s, %s)
                    RETURNING *
                    """,
                    (tenant_id, event_type, source_id, Jsonb(payload)),
                )
                event = cur.fetchone()
                cur.execute(
                    """
                    INSERT INTO clinical.webhook_delivery
                        (tenant_id, event_id, endpoint_id, event_type, url, payload)
                    SELECT tenant_id, %s, endpoint_id, %s, url, %s
                      FROM clinical.webhook_endpoint
                     WHERE enabled = true AND event_types ? %s
                    """,
                    (event["event_id"], event_type, Jsonb(payload), event_type),
                )
                return _jsonable(event)

    def list_events(self, *, tenant_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        with self._conn() as conn:
            with self._session(conn, tenant_id) as cur:
                cur.execute(
                    "SELECT * FROM clinical.webhook_event ORDER BY created_at DESC LIMIT %s",
                    (limit,),
                )
                return [_jsonable(row) for row in cur.fetchall()]

    def list_deliveries(self, *, tenant_id: str, state: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
        clauses = []
        params: List[Any] = []
        if state is not None:
            clauses.append("state = %s")
            params.append(state)
        params.append(limit)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._conn() as conn:
            with self._session(conn, tenant_id) as cur:
                cur.execute(
                    f"SELECT * FROM clinical.webhook_delivery {where} ORDER BY created_at DESC LIMIT %s",
                    params,
                )
                return [_jsonable(row) for row in cur.fetchall()]

    def due_deliveries(self, *, limit: int = 100, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
        # Cross-tenant background sweep: deliberately NOT inside a tenant_session, so
        # the worker sees every tenant's pending deliveries. With FORCE ROW LEVEL
        # SECURITY this requires the worker's connection role to be a superuser or hold
        # BYPASSRLS (see deploy/migrations/007_force_rls.sql and docs/deployment.md);
        # per-request handlers stay confined by the non-privileged RECLASS_DB_ROLE.
        clock = now or _now()
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT d.*, e.secret
                      FROM clinical.webhook_delivery d
                      JOIN clinical.webhook_endpoint e ON e.endpoint_id = d.endpoint_id
                     WHERE d.state IN ('pending', 'retry')
                       AND (d.next_attempt_at IS NULL OR d.next_attempt_at <= %s)
                     ORDER BY d.created_at
                     LIMIT %s
                    """,
                    (clock, limit),
                )
                return [_jsonable(row) for row in cur.fetchall()]

    def mark_delivery_attempt(
        self,
        *,
        delivery_id: str,
        state: str,
        status_code: Optional[int],
        response_body: str,
        next_attempt_at: Optional[datetime],
    ) -> Dict[str, Any]:
        # Cross-tenant worker write (pairs with due_deliveries); same privileged-role
        # requirement under FORCE ROW LEVEL SECURITY.
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE clinical.webhook_delivery
                       SET state = %s,
                           attempts = attempts + 1,
                           last_status_code = %s,
                           last_response_body = %s,
                           next_attempt_at = %s,
                           delivered_at = CASE WHEN %s = 'delivered' THEN now() ELSE delivered_at END
                     WHERE delivery_id = %s
                    RETURNING *
                    """,
                    (state, status_code, response_body[:2000], next_attempt_at, state, delivery_id),
                )
                row = cur.fetchone()
                if row is None:
                    raise LookupError(f"webhook delivery not found: {delivery_id}")
                return _jsonable(row)


def next_retry_at(attempts_after_failure: int, *, now: Optional[datetime] = None) -> datetime:
    delay = min(3600, 2 ** max(0, attempts_after_failure - 1))
    return (now or _now()) + timedelta(seconds=delay)
