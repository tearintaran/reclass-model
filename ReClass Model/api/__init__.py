"""Tenant-aware service surface for the reclassification engine.

This package is a *thin* layer over the existing, deterministic core: it resolves
evidence through the existing ``evidence`` providers, scores through
``engine.scoring.classify``, persists through ``storage.*`` on an RLS-enforced
``storage.db.tenant_session``, and reanalyses through ``monitoring.reanalysis``.
It never re-implements scoring or identity logic.

Two design choices make every endpoint testable without a live database:

  * all clinical persistence goes through a small :class:`api.store.ClinicalStore`
    abstraction with a real (``DbClinicalStore``) and an in-memory
    (``InMemoryClinicalStore``) implementation, and
  * evidence resolution goes through a configurable :class:`api.evidence_resolver`
    so tests can inject deterministic providers (match / absent / failure).
"""

from __future__ import annotations

__all__ = ["create_app"]


def create_app(*args, **kwargs):  # pragma: no cover - thin re-export
    """Lazy re-export of :func:`api.app.create_app` (avoids import cycles)."""
    from .app import create_app as _create_app

    return _create_app(*args, **kwargs)
