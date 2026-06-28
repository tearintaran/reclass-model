"""Runtime settings for the API (environment-driven, with safe defaults).

Kept intentionally tiny: the API has almost no configuration of its own because
it delegates to the existing engine/storage layers. The two switches that matter
are which PostgreSQL database the real store connects to and whether the
development-only ``/validation/run`` endpoint is reachable.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, FrozenSet, Tuple


def _parse_subjects(raw: str | None) -> FrozenSet[str]:
    """Parse a platform-operator subject allowlist (comma- or JSON-list-encoded)."""
    if not raw:
        return frozenset()
    raw = raw.strip()
    if raw.startswith("["):
        data = json.loads(raw)
        return frozenset(str(s).strip() for s in data if str(s).strip())
    return frozenset(s.strip() for s in raw.split(",") if s.strip())


def _parse_api_keys(raw: str | None) -> Dict[str, dict]:
    if not raw:
        return {}
    data = json.loads(raw)
    return data if isinstance(data, dict) else {}


def _parse_jwks(raw: str | None) -> Dict[str, Any]:
    if not raw:
        return {}
    data = json.loads(raw)
    return data if isinstance(data, dict) else {}


def _parse_roles(raw: str | None) -> Tuple[str, ...]:
    if not raw:
        return ("reviewer",)
    return tuple(r.strip() for r in raw.split(",") if r.strip())


@dataclass(frozen=True)
class Settings:
    """Immutable API settings resolved once at app construction."""

    #: ``development`` | ``staging`` | ``production``. Gates dev-only endpoints.
    environment: str = "development"
    #: PostgreSQL database the real (DB-backed) store connects to.
    db_name: str = "reclass_dev"
    #: Optional non-superuser role to ``SET LOCAL ROLE`` so RLS is enforced even
    #: when the connecting role is a superuser/owner (see ``storage.db``).
    db_role: str | None = None
    #: HTTP header carrying the caller's tenant id (a UUID).
    tenant_header: str = "X-Tenant-Id"
    #: HS256 secret for Bearer JWT validation (empty disables JWT).
    jwt_secret: str = ""
    #: OIDC / asymmetric (RS256) validation against an identity provider's JWKS.
    #: ``oidc_issuer`` + (``oidc_jwks_url`` OR ``oidc_jwks``) enables it; ``oidc_audience``
    #: is checked when set. Empty disables OIDC (HS256 + API keys still apply).
    oidc_issuer: str = ""
    oidc_audience: str = ""
    oidc_jwks_url: str = ""
    #: Pinned/static JWKS (``{"keys": [...]}``), e.g. for air-gapped deploys or tests.
    oidc_jwks: Dict[str, Any] = field(default_factory=dict)
    #: Static API keys: ``{token: {tenant_id, roles, user_id?}}``.
    api_keys: Dict[str, dict] = field(default_factory=dict)
    #: ``auto`` accepts OIDC, HS256, and static API-key bearer tokens. ``oidc``
    #: is the production fail-closed mode: only RS256/JWKS OIDC bearer tokens
    #: are accepted and HS256/API-key fallback is disabled.
    auth_mode: str = "auto"
    #: Platform-operator (cross-tenant registry administration) allowlist, by token
    #: ``sub``. A tenant ``admin`` role is NOT sufficient for platform operations in
    #: production: the principal must be on this server-configured list. Empty ⇒ no
    #: platform operators outside development (fail closed). See ``authz``.
    platform_admin_subjects: FrozenSet[str] = field(default_factory=frozenset)
    #: When set, a platform operator's token issuer must equal this (binds the
    #: allowlist to the platform's own IdP, not a tenant IdP).
    platform_oidc_issuer: str = ""
    #: Audit backend name (``memory`` for dev/tests, ``db`` for production).
    audit_backend: str = "memory"
    #: Operational audit retention policy. Memory pruning is entry-count based;
    #: database pruning uses this age threshold when explicitly applied.
    audit_retention_days: int = 365
    #: Roles granted to legacy header-only development sessions.
    legacy_default_roles: Tuple[str, ...] = ("reviewer",)
    #: Max in-memory audit entries before pruning oldest.
    audit_max_entries: int = 10_000
    #: Per-client request limit over a rolling minute. ``0`` disables it.
    rate_limit_per_minute: int = 0
    #: Maximum request body size in bytes based on Content-Length. ``0`` disables it.
    request_size_limit_bytes: int = 0
    #: Opt-in strict preflight gate at app startup.
    preflight_on_startup: bool = False
    #: Include database/RLS/migration-ledger checks in strict preflight.
    preflight_check_database: bool = False
    #: Reference FASTA metadata sidecar written by ``engine.reference_cache``.
    reference_metadata_path: str = "data/reference/GRCh38.fa.meta.json"
    #: Provider-cache manifest path, or a provider-cache directory containing
    #: manifest-like JSON files.
    provider_cache_manifest_path: str = "data/cache/providers"
    #: Restore-test metadata written by deployment runbooks.
    restore_test_metadata_path: str = "deploy/restore-last-tested.json"

    @property
    def is_development(self) -> bool:
        return self.environment.strip().lower() == "development"

    @property
    def is_production(self) -> bool:
        return self.environment.strip().lower() == "production"

    @property
    def requires_oidc_auth(self) -> bool:
        return self.auth_mode.strip().lower() in {"oidc", "strict_oidc", "production_oidc"}

    def allows_legacy_tenant_header(self) -> bool:
        """True when unauthenticated ``X-Tenant-Id`` access is permitted."""
        return self.is_development and not self.is_production


def get_settings() -> Settings:
    """Build :class:`Settings` from the environment (used as a FastAPI default)."""
    environment = os.environ.get("RECLASS_API_ENV", "development")
    is_prod = environment.strip().lower() == "production"
    preflight_env = os.environ.get("RECLASS_PREFLIGHT_ON_STARTUP")
    db_preflight_env = os.environ.get("RECLASS_PREFLIGHT_CHECK_DATABASE")
    return Settings(
        environment=environment,
        db_name=os.environ.get("RECLASS_DB", "reclass_dev"),
        db_role=os.environ.get("RECLASS_DB_ROLE") or None,
        tenant_header=os.environ.get("RECLASS_TENANT_HEADER", "X-Tenant-Id"),
        jwt_secret=os.environ.get("RECLASS_JWT_SECRET", ""),
        oidc_issuer=os.environ.get("RECLASS_OIDC_ISSUER", ""),
        oidc_audience=os.environ.get("RECLASS_OIDC_AUDIENCE", ""),
        oidc_jwks_url=os.environ.get("RECLASS_OIDC_JWKS_URL", ""),
        oidc_jwks=_parse_jwks(os.environ.get("RECLASS_OIDC_JWKS")),
        api_keys=_parse_api_keys(os.environ.get("RECLASS_API_KEYS")),
        auth_mode=os.environ.get("RECLASS_AUTH_MODE") or ("oidc" if is_prod else "auto"),
        platform_admin_subjects=_parse_subjects(os.environ.get("RECLASS_PLATFORM_ADMINS")),
        platform_oidc_issuer=os.environ.get("RECLASS_PLATFORM_OIDC_ISSUER", ""),
        audit_backend=os.environ.get("RECLASS_AUDIT_BACKEND", "memory"),
        audit_retention_days=int(os.environ.get("RECLASS_AUDIT_RETENTION_DAYS", "365")),
        legacy_default_roles=_parse_roles(os.environ.get("RECLASS_LEGACY_ROLES")),
        audit_max_entries=int(os.environ.get("RECLASS_AUDIT_MAX_ENTRIES", "10000")),
        rate_limit_per_minute=int(os.environ.get("RECLASS_RATE_LIMIT_PER_MINUTE", "600" if is_prod else "0")),
        request_size_limit_bytes=int(os.environ.get("RECLASS_REQUEST_SIZE_LIMIT_BYTES", "1048576" if is_prod else "0")),
        preflight_on_startup=(
            is_prod
            if preflight_env is None
            else preflight_env.strip().lower() in {"1", "true", "yes"}
        ),
        preflight_check_database=(
            is_prod
            if db_preflight_env is None
            else db_preflight_env.strip().lower() in {"1", "true", "yes"}
        ),
        reference_metadata_path=os.environ.get(
            "RECLASS_REFERENCE_METADATA", "data/reference/GRCh38.fa.meta.json"
        ),
        provider_cache_manifest_path=os.environ.get(
            "RECLASS_PROVIDER_CACHE_MANIFEST", "data/cache/providers"
        ),
        restore_test_metadata_path=os.environ.get(
            "RECLASS_RESTORE_TEST_METADATA", "deploy/restore-last-tested.json"
        ),
    )


@dataclass(frozen=True)
class PreflightFailure:
    """One named production-readiness failure."""

    name: str
    message: str

    def to_dict(self) -> Dict[str, str]:
        return {"name": self.name, "message": self.message}


class PreflightError(RuntimeError):
    """Raised when strict startup preflight fails."""

    def __init__(self, failures: Tuple[PreflightFailure, ...]):
        self.failures = failures
        joined = "; ".join(f"{f.name}: {f.message}" for f in failures)
        super().__init__(f"preflight failed: {joined}")


def _resolve_path(path: str, base_path: Path) -> Path:
    p = Path(path)
    return p if p.is_absolute() else base_path / p


def _json_file_ok(path: Path, required_keys: Tuple[str, ...]) -> bool:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return isinstance(data, dict) and all(data.get(k) not in (None, "") for k in required_keys)


def _json_file(path: Path) -> Dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _provider_manifest_ok(path: Path) -> bool:
    if path.is_file():
        return _json_file_ok(path, ("source", "version", "sha256")) or _json_file_ok(
            path, ("provider",)
        ) or _json_file_ok(
            path, ("source", "source_version", "checksum")
        )
    if not path.is_dir():
        return False
    candidates = [
        p for p in path.iterdir()
        if p.is_file() and (
            p.name.endswith(".manifest.json")
            or p.name == "manifest.json"
            or p.name.endswith("_manifest.json")
        )
    ]
    return any(_provider_manifest_ok(p) for p in candidates)


def _provider_manifest_count(path: Path) -> int:
    if path.is_file():
        return 1 if _provider_manifest_ok(path) else 0
    if not path.is_dir():
        return 0
    return sum(
        1
        for p in path.iterdir()
        if p.is_file()
        and (
            p.name.endswith(".manifest.json")
            or p.name == "manifest.json"
            or p.name.endswith("_manifest.json")
        )
        and _provider_manifest_ok(p)
    )


def _database_preflight_failures(settings: Settings) -> list[PreflightFailure]:
    failures: list[PreflightFailure] = []
    try:
        from db.apply import discover_migrations
        from storage.db import connect

        with connect(settings.db_name, autocommit=True) as conn:
            with conn.cursor() as cur:
                if settings.db_role:
                    cur.execute(
                        "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = %s",
                        (settings.db_role,),
                    )
                    role_row = cur.fetchone()
                    if role_row is None:
                        failures.append(PreflightFailure(
                            "database_role",
                            f"configured RECLASS_DB_ROLE does not exist: {settings.db_role}",
                        ))
                    elif role_row["rolsuper"] or role_row["rolbypassrls"]:
                        # The per-request tenant role must be unable to bypass RLS;
                        # a superuser/BYPASSRLS role would see every tenant's rows.
                        failures.append(PreflightFailure(
                            "database_role_privileges",
                            f"RECLASS_DB_ROLE {settings.db_role!r} must not be a superuser "
                            "or have BYPASSRLS (it would bypass row-level security and "
                            "break tenant isolation)",
                        ))
                expected_rls = (
                    ("clinical", "patient"),
                    ("clinical", "classification"),
                    ("clinical", "alert"),
                    ("clinical", "audit_log"),
                )
                for schema, table in expected_rls:
                    cur.execute(
                        """
                        SELECT c.relrowsecurity, c.relforcerowsecurity
                          FROM pg_class c
                          JOIN pg_namespace n ON n.oid = c.relnamespace
                         WHERE n.nspname = %s AND c.relname = %s
                        """,
                        (schema, table),
                    )
                    row = cur.fetchone()
                    if row is None or not row["relrowsecurity"]:
                        failures.append(PreflightFailure(
                            "row_level_security",
                            f"RLS is not enabled for {schema}.{table}",
                        ))
                    elif not row["relforcerowsecurity"]:
                        # ENABLE alone is bypassed by the table owner; FORCE is required
                        # so the policy holds even for an owner/misconfigured role.
                        failures.append(PreflightFailure(
                            "row_level_security",
                            f"RLS is not FORCEd for {schema}.{table} "
                            "(owner or misconfigured role could bypass tenant isolation)",
                        ))
                migrations = discover_migrations()
                if migrations:
                    latest = migrations[-1]
                    cur.execute(
                        """
                        SELECT checksum_sha256, status
                          FROM public.reclass_schema_migrations
                         WHERE migration_id = %s
                        """,
                        (latest.migration_id,),
                    )
                    row = cur.fetchone()
                    if row is None:
                        failures.append(PreflightFailure(
                            "migration_ledger",
                            f"latest migration is not recorded: {latest.filename}",
                        ))
                    elif row["checksum_sha256"] != latest.checksum_sha256 or row["status"] != "applied":
                        failures.append(PreflightFailure(
                            "migration_ledger",
                            f"migration ledger mismatch for {latest.filename}",
                        ))
    except Exception as exc:
        failures.append(PreflightFailure(
            "database_preflight",
            f"database/RLS/migration-ledger checks failed: {exc}",
        ))
    return failures


def preflight_check(
    settings: Settings,
    *,
    environ: Dict[str, str] | None = None,
    base_path: str | Path | None = None,
) -> Tuple[PreflightFailure, ...]:
    """Run production-readiness checks and return named failures.

    The function is pure and explicit so tests and deployment tooling can call it
    without constructing a FastAPI app. ``create_app`` can also run it at startup
    when ``Settings.preflight_on_startup`` is true.
    """
    env = environ if environ is not None else dict(os.environ)
    base = Path(base_path) if base_path is not None else Path.cwd()
    failures: list[PreflightFailure] = []

    missing = [
        name for name in ("RECLASS_API_ENV", "RECLASS_DB", "RECLASS_DB_ROLE")
        if not str(env.get(name, "")).strip()
    ]
    if settings.is_production and missing:
        failures.append(PreflightFailure(
            "required_environment_variables",
            "missing required production environment variables: " + ", ".join(missing),
        ))

    if settings.is_production and not settings.requires_oidc_auth:
        failures.append(PreflightFailure(
            "production_auth_mode",
            "production requires RECLASS_AUTH_MODE=oidc so HS256/API-key fallback is disabled",
        ))

    if (settings.is_production or settings.requires_oidc_auth) and not (
        settings.oidc_issuer and (settings.oidc_jwks_url or settings.oidc_jwks)
    ):
        failures.append(PreflightFailure(
            "oidc_jwks_configuration",
            "production requires RECLASS_OIDC_ISSUER and RECLASS_OIDC_JWKS_URL or RECLASS_OIDC_JWKS",
        ))

    if settings.audit_backend.strip().lower() not in {"memory", "db"}:
        failures.append(PreflightFailure(
            "audit_backend",
            "RECLASS_AUDIT_BACKEND must be one of: memory, db",
        ))
    elif settings.is_production and settings.audit_backend.strip().lower() != "db":
        failures.append(PreflightFailure(
            "audit_backend",
            "production requires persistent audit backend RECLASS_AUDIT_BACKEND=db",
        ))

    if settings.is_production and not settings.db_role:
        failures.append(PreflightFailure(
            "database_role",
            "production requires RECLASS_DB_ROLE so RLS is enforced under a non-owner role",
        ))

    reference_meta = _resolve_path(settings.reference_metadata_path, base)
    if not _json_file_ok(reference_meta, ("build", "fasta_path", "sha256", "version")):
        failures.append(PreflightFailure(
            "reference_fasta_metadata",
            f"missing or invalid reference FASTA metadata: {reference_meta}",
        ))

    provider_manifest = _resolve_path(settings.provider_cache_manifest_path, base)
    if not _provider_manifest_ok(provider_manifest):
        failures.append(PreflightFailure(
            "provider_cache_manifest",
            f"missing provider-cache manifest at {provider_manifest}",
        ))

    if settings.is_production and settings.preflight_check_database:
        failures.extend(_database_preflight_failures(settings))

    return tuple(failures)


def readiness_report(
    settings: Settings,
    *,
    base_path: str | Path | None = None,
    environ: Dict[str, str] | None = None,
) -> Dict[str, Any]:
    """Return structured readiness details for health endpoints and onboarding.

    ``preflight_check`` remains the fail/raise API. This companion exposes the
    same checks plus non-fatal context that operators want on dashboards.
    """
    base = Path(base_path) if base_path is not None else Path.cwd()
    failures = preflight_check(settings, environ=environ, base_path=base)
    by_name = {failure.name: failure.message for failure in failures}
    reference_path = _resolve_path(settings.reference_metadata_path, base)
    provider_path = _resolve_path(settings.provider_cache_manifest_path, base)
    restore_path = _resolve_path(settings.restore_test_metadata_path, base)
    restore_meta = _json_file(restore_path)
    restore_age_days = None
    if restore_meta and restore_meta.get("tested_at"):
        try:
            tested = datetime.fromisoformat(str(restore_meta["tested_at"]).replace("Z", "+00:00"))
            restore_age_days = round((datetime.now(timezone.utc) - tested).total_seconds() / 86400, 2)
        except ValueError:
            restore_age_days = None
    checks = {
        "required_environment_variables": "ok" if "required_environment_variables" not in by_name else "failed",
        "production_auth_mode": "ok" if "production_auth_mode" not in by_name else "failed",
        "oidc_jwks": "ok" if "oidc_jwks_configuration" not in by_name else "failed",
        "audit_backend": "ok" if "audit_backend" not in by_name else "failed",
        "database_role": "ok" if "database_role" not in by_name else "failed",
        "reference_metadata": "ok" if "reference_fasta_metadata" not in by_name else "failed",
        "provider_cache_manifests": "ok" if "provider_cache_manifest" not in by_name else "failed",
        "database_rls_migration_ledger": (
            "not_checked"
            if not settings.preflight_check_database
            else "ok"
            if not any(name in by_name for name in ("database_preflight", "row_level_security", "migration_ledger"))
            else "failed"
        ),
    }
    return {
        "status": "ok" if not failures else "failed",
        "environment": settings.environment,
        "auth_mode": settings.auth_mode,
        "checks": checks,
        "failures": [failure.to_dict() for failure in failures],
        "artifacts": {
            "reference_metadata_path": str(reference_path),
            "reference_metadata_present": reference_path.is_file(),
            "provider_cache_manifest_path": str(provider_path),
            "provider_cache_manifest_count": _provider_manifest_count(provider_path),
            "restore_test_metadata_path": str(restore_path),
            "restore_test_age_days": restore_age_days,
        },
    }


def require_preflight(
    settings: Settings,
    *,
    environ: Dict[str, str] | None = None,
    base_path: str | Path | None = None,
) -> None:
    """Raise :class:`PreflightError` if any readiness check fails."""
    failures = preflight_check(settings, environ=environ, base_path=base_path)
    if failures:
        raise PreflightError(failures)
