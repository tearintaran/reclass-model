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
from pathlib import Path
from typing import Any, Dict, Tuple


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
    #: Audit backend name (``memory`` for dev/tests, ``db`` for production).
    audit_backend: str = "memory"
    #: Roles granted to legacy header-only development sessions.
    legacy_default_roles: Tuple[str, ...] = ("reviewer",)
    #: Max in-memory audit entries before pruning oldest.
    audit_max_entries: int = 10_000
    #: Opt-in strict preflight gate at app startup.
    preflight_on_startup: bool = False
    #: Reference FASTA metadata sidecar written by ``engine.reference_cache``.
    reference_metadata_path: str = "data/reference/GRCh38.fa.meta.json"
    #: Provider-cache manifest path, or a provider-cache directory containing
    #: manifest-like JSON files.
    provider_cache_manifest_path: str = "data/cache/providers"

    @property
    def is_development(self) -> bool:
        return self.environment.strip().lower() == "development"

    @property
    def is_production(self) -> bool:
        return self.environment.strip().lower() == "production"

    def allows_legacy_tenant_header(self) -> bool:
        """True when unauthenticated ``X-Tenant-Id`` access is permitted."""
        return self.is_development and not self.is_production


def get_settings() -> Settings:
    """Build :class:`Settings` from the environment (used as a FastAPI default)."""
    return Settings(
        environment=os.environ.get("RECLASS_API_ENV", "development"),
        db_name=os.environ.get("RECLASS_DB", "reclass_dev"),
        db_role=os.environ.get("RECLASS_DB_ROLE") or None,
        tenant_header=os.environ.get("RECLASS_TENANT_HEADER", "X-Tenant-Id"),
        jwt_secret=os.environ.get("RECLASS_JWT_SECRET", ""),
        oidc_issuer=os.environ.get("RECLASS_OIDC_ISSUER", ""),
        oidc_audience=os.environ.get("RECLASS_OIDC_AUDIENCE", ""),
        oidc_jwks_url=os.environ.get("RECLASS_OIDC_JWKS_URL", ""),
        oidc_jwks=_parse_jwks(os.environ.get("RECLASS_OIDC_JWKS")),
        api_keys=_parse_api_keys(os.environ.get("RECLASS_API_KEYS")),
        audit_backend=os.environ.get("RECLASS_AUDIT_BACKEND", "memory"),
        legacy_default_roles=_parse_roles(os.environ.get("RECLASS_LEGACY_ROLES")),
        audit_max_entries=int(os.environ.get("RECLASS_AUDIT_MAX_ENTRIES", "10000")),
        preflight_on_startup=os.environ.get("RECLASS_PREFLIGHT_ON_STARTUP", "").strip().lower()
        in {"1", "true", "yes"},
        reference_metadata_path=os.environ.get(
            "RECLASS_REFERENCE_METADATA", "data/reference/GRCh38.fa.meta.json"
        ),
        provider_cache_manifest_path=os.environ.get(
            "RECLASS_PROVIDER_CACHE_MANIFEST", "data/cache/providers"
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


def _provider_manifest_ok(path: Path) -> bool:
    if path.is_file():
        return _json_file_ok(path, ("source", "version", "sha256")) or _json_file_ok(
            path, ("provider",)
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

    if settings.is_production and not (
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

    return tuple(failures)


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
