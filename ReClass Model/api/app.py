"""FastAPI application factory.

``create_app`` wires the settings, the clinical store, and the evidence resolver
onto ``app.state`` and mounts the routers. Tests build an app with an injected
in-memory store and a resolver of deterministic providers; ``uvicorn api.app:app``
uses the DB-backed store and an offline provider set.
"""

from __future__ import annotations

from typing import Optional

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.requests import Request

from engine import config as C

from .evidence_resolver import EvidenceResolver
from .settings import Settings, get_settings
from .store import ClinicalStore, DbClinicalStore
from .routers import (
    alerts,
    classifications,
    classify,
    evidence,
    reanalysis,
    reports,
    validation,
)


def build_default_resolver() -> EvidenceResolver:
    """Offline-safe resolver for live serving.

    Registers the REVEL and gnomAD providers from their local caches (an absent
    cache yields an empty, never-raising provider). ClinGen needs a fixture path,
    so it is only registered when ``RECLASS_CLINGEN_FIXTURE`` points at one.
    """
    import os

    resolver = EvidenceResolver()
    try:
        from evidence.revel import RevelProvider

        resolver.register("revel", RevelProvider.from_cache())
    except Exception:  # pragma: no cover - provider optional at serve time
        pass
    try:
        from evidence.gnomad import GnomadProvider

        resolver.register("gnomad", GnomadProvider.offline())
    except Exception:  # pragma: no cover - provider optional at serve time
        pass
    fixture = os.environ.get("RECLASS_CLINGEN_FIXTURE")
    if fixture:
        try:
            from evidence.clingen import ClinGenEvidenceProvider

            resolver.register("clingen", ClinGenEvidenceProvider.from_fixture(fixture))
        except Exception:  # pragma: no cover - fixture optional
            pass
    return resolver


def create_app(
    *,
    settings: Optional[Settings] = None,
    store: Optional[ClinicalStore] = None,
    resolver: Optional[EvidenceResolver] = None,
) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(
        title="Variant Reclassification API",
        version=C.ENGINE_VERSION,
        description="Tenant-aware service surface over the deterministic "
                    "ACMG/AMP reclassification engine. Decision support only — "
                    "no result is clinically released without credentialed sign-off.",
    )

    app.state.settings = settings
    app.state.store = store or DbClinicalStore(
        db_name=settings.db_name, role=settings.db_role
    )
    app.state.resolver = resolver if resolver is not None else build_default_resolver()

    for module in (
        evidence, classify, classifications, reanalysis, alerts, validation, reports,
    ):
        app.include_router(module.router)

    @app.get("/health", tags=["meta"])
    def health() -> dict:
        return {"status": "ok", "engine_version": C.ENGINE_VERSION,
                "environment": settings.environment}

    @app.exception_handler(RuntimeError)
    def _runtime_error_handler(request: Request, exc: RuntimeError) -> JSONResponse:
        # The DB store raises RuntimeError when psycopg/PostgreSQL is unavailable.
        # Surface it as a 503 rather than a 500 so callers can distinguish an
        # infrastructure outage from a request error.
        return JSONResponse(status_code=503, content={"detail": str(exc)})

    return app


# Module-level ASGI app for `uvicorn api.app:app`.
app = create_app()
