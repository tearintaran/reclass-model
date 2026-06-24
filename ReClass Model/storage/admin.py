"""Tenant administration storage for platform onboarding and operations."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Protocol


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _jsonable(row: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(row)
    for key, value in list(out.items()):
        if isinstance(value, (uuid.UUID, datetime)):
            out[key] = value.isoformat() if isinstance(value, datetime) else str(value)
    return out


class TenantAdminStore(Protocol):
    def create_tenant(
        self,
        *,
        name: str,
        slug: str,
        contact_email: Optional[str] = None,
        oidc_issuer: Optional[str] = None,
        oidc_audience: Optional[str] = None,
        status: str = "onboarding",
    ) -> Dict[str, Any]: ...

    def list_tenants(self) -> List[Dict[str, Any]]: ...

    def get_tenant(self, tenant_id: str) -> Optional[Dict[str, Any]]: ...

    def update_tenant(self, tenant_id: str, updates: Dict[str, Any]) -> Dict[str, Any]: ...


class InMemoryTenantAdminStore:
    """Dependency-free tenant-admin store used by tests and no-DB apps."""

    def __init__(self) -> None:
        self._tenants: Dict[str, Dict[str, Any]] = {}

    def create_tenant(
        self,
        *,
        name: str,
        slug: str,
        contact_email: Optional[str] = None,
        oidc_issuer: Optional[str] = None,
        oidc_audience: Optional[str] = None,
        status: str = "onboarding",
    ) -> Dict[str, Any]:
        if any(row["slug"] == slug for row in self._tenants.values()):
            raise ValueError(f"tenant slug already exists: {slug}")
        tenant_id = str(uuid.uuid4())
        row = {
            "tenant_id": tenant_id,
            "name": name,
            "slug": slug,
            "status": status,
            "contact_email": contact_email,
            "oidc_issuer": oidc_issuer,
            "oidc_audience": oidc_audience,
            "created_at": _now(),
            "updated_at": _now(),
        }
        self._tenants[tenant_id] = row
        return _jsonable(row)

    def list_tenants(self) -> List[Dict[str, Any]]:
        return [_jsonable(row) for row in sorted(self._tenants.values(), key=lambda r: r["name"])]

    def get_tenant(self, tenant_id: str) -> Optional[Dict[str, Any]]:
        row = self._tenants.get(str(tenant_id))
        return _jsonable(row) if row else None

    def update_tenant(self, tenant_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
        row = self._tenants.get(str(tenant_id))
        if row is None:
            raise LookupError(f"tenant not found: {tenant_id}")
        if "slug" in updates and any(
            existing_id != tenant_id and existing["slug"] == updates["slug"]
            for existing_id, existing in self._tenants.items()
        ):
            raise ValueError(f"tenant slug already exists: {updates['slug']}")
        allowed = {"name", "slug", "status", "contact_email", "oidc_issuer", "oidc_audience"}
        for key, value in updates.items():
            if key in allowed:
                row[key] = value
        row["updated_at"] = _now()
        return _jsonable(row)


class DbTenantAdminStore:
    """PostgreSQL-backed tenant administration."""

    def __init__(self, *, db_name: str = "reclass_dev", connect=None) -> None:
        self._db_name = db_name
        self._connect = connect

    def _conn(self):
        if self._connect is not None:
            return self._connect()
        from storage.db import connect

        return connect(self._db_name)

    def create_tenant(
        self,
        *,
        name: str,
        slug: str,
        contact_email: Optional[str] = None,
        oidc_issuer: Optional[str] = None,
        oidc_audience: Optional[str] = None,
        status: str = "onboarding",
    ) -> Dict[str, Any]:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO clinical.tenant
                        (name, slug, status, contact_email, oidc_issuer, oidc_audience)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    RETURNING *
                    """,
                    (name, slug, status, contact_email, oidc_issuer, oidc_audience),
                )
                return _jsonable(cur.fetchone())

    def list_tenants(self) -> List[Dict[str, Any]]:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM clinical.tenant ORDER BY name")
                return [_jsonable(row) for row in cur.fetchall()]

    def get_tenant(self, tenant_id: str) -> Optional[Dict[str, Any]]:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM clinical.tenant WHERE tenant_id = %s", (tenant_id,))
                row = cur.fetchone()
                return _jsonable(row) if row else None

    def update_tenant(self, tenant_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
        allowed = {
            "name",
            "slug",
            "status",
            "contact_email",
            "oidc_issuer",
            "oidc_audience",
        }
        pairs = [(key, value) for key, value in updates.items() if key in allowed]
        if not pairs:
            row = self.get_tenant(tenant_id)
            if row is None:
                raise LookupError(f"tenant not found: {tenant_id}")
            return row
        set_sql = ", ".join(f"{key} = %s" for key, _ in pairs)
        params = [value for _, value in pairs]
        params.append(tenant_id)
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    UPDATE clinical.tenant
                       SET {set_sql}, updated_at = now()
                     WHERE tenant_id = %s
                    RETURNING *
                    """,
                    params,
                )
                row = cur.fetchone()
                if row is None:
                    raise LookupError(f"tenant not found: {tenant_id}")
                return _jsonable(row)
