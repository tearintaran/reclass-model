"""Tenant onboarding and pre-production readiness checks."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from engine.scoring import EvidenceEvent, classify

from api.settings import Settings, readiness_report


def source_cache_setup(settings: Settings, *, base_path: str | Path | None = None) -> Dict[str, Any]:
    base = Path(base_path) if base_path is not None else Path.cwd()
    path = Path(settings.provider_cache_manifest_path)
    if not path.is_absolute():
        path = base / path
    manifests = []
    if path.is_file():
        manifests = [path]
    elif path.is_dir():
        manifests = [
            p for p in path.iterdir()
            if p.is_file()
            and (
                p.name.endswith(".manifest.json")
                or p.name == "manifest.json"
                or p.name.endswith("_manifest.json")
            )
        ]
    return {
        "name": "source_cache_setup",
        "ready": bool(manifests),
        "path": str(path),
        "manifest_count": len(manifests),
    }


def reference_cache_verification(settings: Settings, *, base_path: str | Path | None = None) -> Dict[str, Any]:
    base = Path(base_path) if base_path is not None else Path.cwd()
    path = Path(settings.reference_metadata_path)
    if not path.is_absolute():
        path = base / path
    return {
        "name": "reference_cache_verification",
        "ready": path.is_file(),
        "path": str(path),
    }


def oidc_setup_check(settings: Settings) -> Dict[str, Any]:
    return {
        "name": "oidc_setup",
        "ready": bool(settings.oidc_issuer and (settings.oidc_jwks_url or settings.oidc_jwks)),
        "issuer": settings.oidc_issuer,
        "audience": settings.oidc_audience,
        "auth_mode": settings.auth_mode,
    }


def sample_data_smoke_test() -> Dict[str, Any]:
    """Run a deterministic local classification smoke test."""
    result = classify([
        EvidenceEvent(
            source="onboarding",
            acmg_criterion="PVS1",
            evidence_direction="pathogenic",
            applied_strength="very_strong",
            source_version="smoke-test",
        )
    ])
    return {
        "name": "sample_data_smoke_test",
        "ready": result.tier == "Likely Pathogenic",
        "tier": result.tier,
        "reconstruction_hash": result.reconstruction_hash,
    }


def preproduction_readiness_report(
    settings: Settings,
    *,
    tenant: Dict[str, Any] | None = None,
    base_path: str | Path | None = None,
) -> Dict[str, Any]:
    checks = [
        source_cache_setup(settings, base_path=base_path),
        reference_cache_verification(settings, base_path=base_path),
        oidc_setup_check(settings),
        sample_data_smoke_test(),
    ]
    preflight = readiness_report(settings, base_path=base_path)
    ready = all(check["ready"] for check in checks) and preflight["status"] == "ok"
    return {
        "tenant": tenant or {},
        "ready": ready,
        "checks": checks,
        "preflight": preflight,
    }
