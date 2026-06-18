"""FastAPI application factory.

``create_app`` wires the settings, the clinical store, and the evidence resolver
onto ``app.state`` and mounts the routers. Tests build an app with an injected
in-memory store and a resolver of deterministic providers; ``uvicorn api.app:app``
uses the DB-backed store and an offline provider set.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from fastapi import FastAPI
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.requests import Request
from fastapi.staticfiles import StaticFiles

from engine import config as C

from .audit import DbAuditLog, InMemoryAuditLog
from .evidence_resolver import EvidenceResolver
from .observability import RequestMetrics, StructuredLoggingMiddleware
from .settings import Settings, get_settings, preflight_check, require_preflight
from .store import ClinicalStore, DbClinicalStore
from .routers import (
    alerts,
    audit as audit_router,
    classifications,
    classify,
    evidence,
    reanalysis,
    reports,
    validation,
)


def build_default_resolver() -> EvidenceResolver:
    """Offline-safe resolver for live serving."""
    import os

    resolver = EvidenceResolver()
    try:
        from evidence.revel import RevelProvider

        resolver.register("revel", RevelProvider.from_cache())
    except Exception:  # pragma: no cover
        pass
    try:
        from evidence.gnomad import GnomadProvider

        resolver.register("gnomad", GnomadProvider.offline())
    except Exception:  # pragma: no cover
        pass
    fixture = os.environ.get("RECLASS_CLINGEN_FIXTURE")
    if fixture:
        try:
            from evidence.clingen import ClinGenEvidenceProvider

            resolver.register("clingen", ClinGenEvidenceProvider.from_fixture(fixture))
        except Exception:  # pragma: no cover
            pass
    return resolver


def _build_audit_log(settings: Settings):
    backend = settings.audit_backend.strip().lower()
    if backend == "db":
        return DbAuditLog(db_name=settings.db_name, role=settings.db_role)
    return InMemoryAuditLog(max_entries=settings.audit_max_entries)


def create_app(
    *,
    settings: Optional[Settings] = None,
    store: Optional[ClinicalStore] = None,
    resolver: Optional[EvidenceResolver] = None,
    audit_log=None,
) -> FastAPI:
    settings = settings or get_settings()
    logging.basicConfig(level=logging.INFO)

    app = FastAPI(
        title="Variant Reclassification API",
        version=C.ENGINE_VERSION,
        description="Tenant-aware service surface over the deterministic "
                    "ACMG/AMP reclassification engine. Decision support only — "
                    "no result is clinically released without credentialed sign-off.",
    )

    metrics = RequestMetrics()
    app.state.settings = settings
    app.state.store = store or DbClinicalStore(
        db_name=settings.db_name, role=settings.db_role
    )
    app.state.resolver = resolver if resolver is not None else build_default_resolver()
    app.state.audit_log = audit_log or _build_audit_log(settings)
    app.state.metrics = metrics

    app.add_middleware(StructuredLoggingMiddleware, metrics=metrics)

    for module in (
        evidence, classify, classifications, reanalysis, alerts,
        validation, reports, audit_router,
    ):
        app.include_router(module.router)

    if settings.preflight_on_startup:
        @app.on_event("startup")
        def _strict_preflight() -> None:
            require_preflight(settings, base_path=Path(__file__).resolve().parent.parent)

    frontend_dir = Path(__file__).resolve().parent.parent / "frontend"
    if frontend_dir.is_dir():
        app.mount("/reviewer", StaticFiles(directory=str(frontend_dir), html=True), name="reviewer")

    @app.get("/health", tags=["meta"])
    def health() -> dict:
        return {
            "status": "ok",
            "engine_version": C.ENGINE_VERSION,
            "environment": settings.environment,
            "requests_total": metrics.requests_total,
        }

    @app.get("/health/preflight", tags=["meta"])
    def preflight() -> dict:
        failures = preflight_check(
            settings,
            base_path=Path(__file__).resolve().parent.parent,
        )
        return {
            "status": "ok" if not failures else "failed",
            "failures": [failure.to_dict() for failure in failures],
        }

    @app.get("/metrics", tags=["meta"])
    def metrics_endpoint() -> PlainTextResponse:
        return PlainTextResponse(metrics.prometheus_text(), media_type="text/plain")

    @app.exception_handler(RuntimeError)
    def _runtime_error_handler(request: Request, exc: RuntimeError) -> JSONResponse:
        return JSONResponse(status_code=503, content={"detail": str(exc)})

    return app


app = create_app()
