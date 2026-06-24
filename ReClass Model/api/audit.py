"""Operational audit log for sign-off, alert changes, and reanalysis actions.

Retention is bounded by ``max_entries`` (in-memory) or database policy (see
``deploy/migrations/001_audit_log.sql``). Entries are append-only.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Protocol


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class AuditEntry:
    audit_id: str
    tenant_id: str
    actor_id: str
    action: str
    resource_type: str
    resource_id: str
    detail: Dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=_now)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "audit_id": self.audit_id,
            "tenant_id": self.tenant_id,
            "actor_id": self.actor_id,
            "action": self.action,
            "resource_type": self.resource_type,
            "resource_id": self.resource_id,
            "detail": dict(self.detail),
            "created_at": self.created_at.isoformat(),
        }


@dataclass(frozen=True)
class SecurityEvent:
    """Structured security event carried through the operational audit log."""

    event_type: str
    outcome: str
    actor_id: str = "system"
    resource_type: str = "security"
    resource_id: str = "platform"
    detail: Dict[str, Any] = field(default_factory=dict)

    def audit_action(self) -> str:
        return f"security.{self.event_type}"

    def to_detail(self) -> Dict[str, Any]:
        detail = dict(self.detail)
        detail["outcome"] = self.outcome
        return detail


class AuditLog(Protocol):
    def append(
        self,
        *,
        tenant_id: str,
        actor_id: str,
        action: str,
        resource_type: str,
        resource_id: str,
        detail: Optional[Dict[str, Any]] = None,
    ) -> AuditEntry: ...

    def list_entries(
        self,
        *,
        tenant_id: str,
        action: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]: ...

    def prune_before(self, *, tenant_id: str, before: datetime) -> int: ...


class InMemoryAuditLog:
    """Bounded, tenant-partitioned audit store for tests and no-DB environments."""

    def __init__(self, *, max_entries: int = 10_000) -> None:
        self._max_entries = max_entries
        self._entries: List[AuditEntry] = []

    def append(
        self,
        *,
        tenant_id: str,
        actor_id: str,
        action: str,
        resource_type: str,
        resource_id: str,
        detail: Optional[Dict[str, Any]] = None,
    ) -> AuditEntry:
        entry = AuditEntry(
            audit_id=str(uuid.uuid4()),
            tenant_id=tenant_id,
            actor_id=actor_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            detail=dict(detail or {}),
        )
        self._entries.append(entry)
        if len(self._entries) > self._max_entries:
            self._entries = self._entries[-self._max_entries :]
        return entry

    def list_entries(
        self,
        *,
        tenant_id: str,
        action: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        rows = [e for e in self._entries if e.tenant_id == tenant_id]
        if action is not None:
            rows = [e for e in rows if e.action == action]
        rows = rows[-limit:]
        return [e.to_dict() for e in reversed(rows)]

    def prune_before(self, *, tenant_id: str, before: datetime) -> int:
        before = before if before.tzinfo else before.replace(tzinfo=timezone.utc)
        before_count = len(self._entries)
        self._entries = [
            entry for entry in self._entries
            if entry.tenant_id != tenant_id or entry.created_at >= before
        ]
        return before_count - len(self._entries)


class DbAuditLog:
    """PostgreSQL-backed audit log (requires ``deploy/migrations/001_audit_log.sql``)."""

    def __init__(self, *, db_name: str, role: Optional[str] = None, connect=None) -> None:
        self._db_name = db_name
        self._role = role
        self._connect = connect

    def _conn(self):
        if self._connect is not None:
            return self._connect()
        from storage.db import connect

        return connect(self._db_name)

    def append(
        self,
        *,
        tenant_id: str,
        actor_id: str,
        action: str,
        resource_type: str,
        resource_id: str,
        detail: Optional[Dict[str, Any]] = None,
    ) -> AuditEntry:
        from psycopg.types.json import Jsonb
        from storage.db import tenant_session

        entry = AuditEntry(
            audit_id=str(uuid.uuid4()),
            tenant_id=tenant_id,
            actor_id=actor_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            detail=dict(detail or {}),
        )
        with self._conn() as conn:
            with tenant_session(conn, tenant_id, role=self._role) as cur:
                cur.execute(
                    """
                    INSERT INTO clinical.audit_log
                        (audit_id, tenant_id, actor_id, action,
                         resource_type, resource_id, detail)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        entry.audit_id,
                        tenant_id,
                        actor_id,
                        action,
                        resource_type,
                        resource_id,
                        Jsonb(entry.detail),
                    ),
                )
        return entry

    def list_entries(
        self,
        *,
        tenant_id: str,
        action: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        from storage.db import tenant_session

        clauses = ["tenant_id = %s"]
        params: List[Any] = [tenant_id]
        if action is not None:
            clauses.append("action = %s")
            params.append(action)
        params.append(limit)
        sql = (
            "SELECT audit_id, tenant_id, actor_id, action, resource_type, "
            "resource_id, detail, created_at "
            f"FROM clinical.audit_log WHERE {' AND '.join(clauses)} "
            "ORDER BY created_at DESC LIMIT %s"
        )
        with self._conn() as conn:
            with tenant_session(conn, tenant_id, role=self._role) as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()
        out: List[Dict[str, Any]] = []
        for row in rows:
            d = dict(row)
            if hasattr(d.get("created_at"), "isoformat"):
                d["created_at"] = d["created_at"].isoformat()
            d["audit_id"] = str(d["audit_id"])
            d["tenant_id"] = str(d["tenant_id"])
            out.append(d)
        return out

    def prune_before(self, *, tenant_id: str, before: datetime) -> int:
        from storage.db import tenant_session

        with self._conn() as conn:
            with tenant_session(conn, tenant_id, role=self._role) as cur:
                cur.execute(
                    "DELETE FROM clinical.audit_log "
                    "WHERE tenant_id = %s AND created_at < %s",
                    (tenant_id, before),
                )
                return int(cur.rowcount or 0)


def append_security_event(
    audit: AuditLog,
    *,
    tenant_id: str,
    event_type: str,
    outcome: str,
    actor_id: str = "system",
    resource_type: str = "security",
    resource_id: str = "platform",
    detail: Optional[Dict[str, Any]] = None,
) -> AuditEntry:
    """Append a structured ``security.*`` audit entry."""
    event = SecurityEvent(
        event_type=event_type,
        outcome=outcome,
        actor_id=actor_id,
        resource_type=resource_type,
        resource_id=resource_id,
        detail=dict(detail or {}),
    )
    return audit.append(
        tenant_id=tenant_id,
        actor_id=event.actor_id,
        action=event.audit_action(),
        resource_type=event.resource_type,
        resource_id=event.resource_id,
        detail=event.to_detail(),
    )


def apply_retention_policy(
    audit: AuditLog,
    *,
    tenant_id: str,
    retention_days: int,
    now: datetime | None = None,
) -> int:
    """Prune entries older than the configured retention age."""
    clock = now or _now()
    cutoff = clock - timedelta(days=max(0, int(retention_days)))
    return audit.prune_before(tenant_id=tenant_id, before=cutoff)
