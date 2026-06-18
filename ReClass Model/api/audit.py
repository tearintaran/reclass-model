"""Operational audit log for sign-off, alert changes, and reanalysis actions.

Retention is bounded by ``max_entries`` (in-memory) or database policy (see
``deploy/migrations/001_audit_log.sql``). Entries are append-only.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
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
