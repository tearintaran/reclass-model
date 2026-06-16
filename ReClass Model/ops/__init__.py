"""Operational reanalysis: scheduling, work queues, and run reporting (gap §5).

This package turns the reanalysis *primitives* in ``monitoring/reanalysis.py`` into a
runnable, scheduled workflow:

  * :mod:`ops.queue`        — input queues / manifests of variants needing reanalysis
                              (an in-memory queue + a backing ``clinical.reanalysis_queue``).
  * :mod:`ops.run_report`   — per-run roll-ups (checked / unchanged / same-tier /
                              crossing / failed / skipped) with deterministic reasons.
  * :mod:`ops.scheduler`    — trigger detection (provider-version / evidence /
                              config-version changes), the operational run loop, and
                              retry/error handling.

Design rules (mirrored from the existing storage/monitoring split):

  * Pure, stdlib-only logic at the top so dry runs and unit tests import without
    psycopg/PostgreSQL. Anything that touches the database is passed an open cursor.
  * The reanalysis core (``monitoring.reanalysis.reanalyze``) is *called*, never
    reimplemented — the churn-free / crossing-only / audited-same-tier behavior lives
    there and is preserved.
"""

from __future__ import annotations

from ops.run_report import RunReport, VariantOutcome
from ops.scheduler import (
    CONFIG_VERSION_CHANGED,
    INVALID_VARIANT_IDENTITY,
    MISSING_PROVIDER_CACHE,
    NO_EVIDENCE,
    NOT_APPLICABLE,
    UNAVAILABLE_REFERENCE,
    InvalidVariantIdentity,
    MissingProviderCache,
    ReanalysisError,
    SkipReanalysis,
    UnavailableReference,
    config_version_changed,
    execute_run,
    provider_version_changes,
)

__all__ = [
    "RunReport",
    "VariantOutcome",
    "ReanalysisError",
    "SkipReanalysis",
    "MissingProviderCache",
    "UnavailableReference",
    "InvalidVariantIdentity",
    "MISSING_PROVIDER_CACHE",
    "UNAVAILABLE_REFERENCE",
    "INVALID_VARIANT_IDENTITY",
    "CONFIG_VERSION_CHANGED",
    "NO_EVIDENCE",
    "NOT_APPLICABLE",
    "provider_version_changes",
    "config_version_changed",
    "execute_run",
]
