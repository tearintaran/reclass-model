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
from .observability import RequestMetrics, StructuredLoggingMiddleware, collect_slo_gauges
from .ratelimit import RateLimitMiddleware, RequestSizeLimitMiddleware
from .settings import Settings, get_settings, readiness_report, require_preflight
from .store import ClinicalStore, DbClinicalStore
from .routers import (
    admin,
    alerts,
    audit as audit_router,
    classifications,
    classify,
    evidence,
    reanalysis,
    reports,
    validation,
    webhooks,
    worklist,
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


def _build_admin_store(settings: Settings):
    if settings.audit_backend.strip().lower() == "db":
        from storage.admin import DbTenantAdminStore

        return DbTenantAdminStore(db_name=settings.db_name)
    from storage.admin import InMemoryTenantAdminStore

    return InMemoryTenantAdminStore()


def _build_webhook_store(settings: Settings):
    if settings.audit_backend.strip().lower() == "db":
        from storage.webhooks import DbWebhookStore

        return DbWebhookStore(db_name=settings.db_name, role=settings.db_role)
    from storage.webhooks import InMemoryWebhookStore

    return InMemoryWebhookStore()


def _build_workbench_store(settings: Settings):
    if settings.audit_backend.strip().lower() == "db":
        from evidence.workbench import DbWorkbenchStore

        return DbWorkbenchStore(db_name=settings.db_name, role=settings.db_role)
    from evidence.workbench import InMemoryWorkbenchStore

    return InMemoryWorkbenchStore()


def _build_worklist_store(settings: Settings):
    if settings.audit_backend.strip().lower() == "db":
        from worklist.case import DbWorklistStore

        return DbWorklistStore(db_name=settings.db_name, role=settings.db_role)
    from worklist.case import InMemoryWorklistStore

    return InMemoryWorklistStore()


def create_app(
    *,
    settings: Optional[Settings] = None,
    store: Optional[ClinicalStore] = None,
    resolver: Optional[EvidenceResolver] = None,
    audit_log=None,
    admin_store=None,
    webhook_store=None,
    workbench_store=None,
    worklist_store=None,
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
    app.state.base_path = Path(__file__).resolve().parent.parent
    app.state.store = store or DbClinicalStore(
        db_name=settings.db_name, role=settings.db_role
    )
    app.state.resolver = resolver if resolver is not None else build_default_resolver()
    app.state.audit_log = audit_log or _build_audit_log(settings)
    app.state.admin_store = admin_store or _build_admin_store(settings)
    app.state.webhook_store = webhook_store or _build_webhook_store(settings)
    app.state.workbench_store = workbench_store or _build_workbench_store(settings)
    app.state.worklist_store = worklist_store or _build_worklist_store(settings)
    app.state.metrics = metrics

    app.add_middleware(StructuredLoggingMiddleware, metrics=metrics)
    if settings.rate_limit_per_minute:
        app.add_middleware(
            RateLimitMiddleware,
            requests_per_minute=settings.rate_limit_per_minute,
        )
    if settings.request_size_limit_bytes:
        app.add_middleware(
            RequestSizeLimitMiddleware,
            max_bytes=settings.request_size_limit_bytes,
        )

    for module in (
        admin,
        evidence, classify, classifications, reanalysis, alerts,
        validation, reports, audit_router, webhooks, worklist,
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
        readiness = readiness_report(
            settings,
            base_path=Path(__file__).resolve().parent.parent,
        )
        return {
            "status": "ok",
            "engine_version": C.ENGINE_VERSION,
            "environment": settings.environment,
            "requests_total": metrics.requests_total,
            "preflight_status": readiness["status"],
            "readiness": readiness["checks"],
        }

    @app.get("/health/preflight", tags=["meta"])
    def preflight() -> dict:
        return readiness_report(
            settings,
            base_path=Path(__file__).resolve().parent.parent,
        )

    @app.get("/metrics", tags=["meta"])
    def metrics_endpoint() -> PlainTextResponse:
        for name, value in collect_slo_gauges(
            settings,
            store=app.state.store,
            base_path=Path(__file__).resolve().parent.parent,
        ).items():
            metrics.set_gauge(name, value)
        return PlainTextResponse(metrics.prometheus_text(), media_type="text/plain")

    @app.exception_handler(RuntimeError)
    def _runtime_error_handler(request: Request, exc: RuntimeError) -> JSONResponse:
        return JSONResponse(status_code=503, content={"detail": str(exc)})

    return app


app = create_app()
