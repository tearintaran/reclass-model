"""Continuous-reanalysis scheduling, the run loop, and error handling (gap §5).

Three concerns, all stdlib-only at the top so dry runs and unit tests import without
psycopg/PostgreSQL:

  * **Trigger detection** — decide *what* needs reanalysis by comparing a previously
    seen state to the current one: provider-version changes (e.g. ``gnomAD 4.0 ->
    4.1``), a config/engine-version change, or named evidence changes. These are pure
    functions of two snapshots.
  * **The run loop** (:func:`execute_run`) — iterate work items, resolve each item's
    current evidence, call ``monitoring.reanalysis.reanalyze`` (the churn-free /
    crossing-only / audited-same-tier core, *not* reimplemented here), and tally a
    :class:`ops.run_report.RunReport`.
  * **Retry / error handling** — a deterministic error taxonomy for missing provider
    caches, unavailable references, and invalid variant identities. Transient errors
    are retried up to ``max_attempts``; a deterministic error (invalid identity)
    fails immediately with a stable reason code so reruns are reproducible.

:func:`run_from_queue` wires these to the DB-backed ``clinical.reanalysis_queue`` /
``clinical.reanalysis_run`` tables when given an open, tenant-scoped cursor.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from engine import config as C

from ops import queue as q
from ops import run_report as rr
from ops.run_report import RunReport, VariantOutcome

# Triggers the scheduler understands (mirrors ops.queue._VALID_TRIGGERS).
TRIGGERS = ("evidence", "provider_version", "config_version")

# --------------------------------------------------------------------------- #
# Deterministic reason codes (failed/skipped)                                  #
# --------------------------------------------------------------------------- #
MISSING_PROVIDER_CACHE = "missing_provider_cache"      # a provider cache file/entry is absent
UNAVAILABLE_REFERENCE = "unavailable_reference"        # GRCh38 FASTA / reference not loadable
INVALID_VARIANT_IDENTITY = "invalid_variant_identity"  # variant key/coords cannot be resolved
CONFIG_VERSION_CHANGED = "config_version_changed"      # informational trigger reason
NO_EVIDENCE = "no_evidence"                            # nothing to score -> skip
NOT_APPLICABLE = "not_applicable"                      # rule/condition not applicable -> skip


# --------------------------------------------------------------------------- #
# Error taxonomy                                                               #
# --------------------------------------------------------------------------- #
class ReanalysisError(Exception):
    """A reanalysis failure carrying a deterministic ``reason_code``.

    ``retryable`` distinguishes transient faults (a cache/reference that may become
    available on a later run) from permanent ones (a malformed variant identity that
    will fail identically every time).
    """

    def __init__(self, reason_code: str, message: str = "", *, retryable: bool = False):
        super().__init__(message or reason_code)
        self.reason_code = reason_code
        self.retryable = retryable


class MissingProviderCache(ReanalysisError):
    """A required provider cache (REVEL/gnomAD/...) is missing — transient."""

    def __init__(self, message: str = "provider cache missing"):
        super().__init__(MISSING_PROVIDER_CACHE, message, retryable=True)


class UnavailableReference(ReanalysisError):
    """The GRCh38 reference (or other external reference) is unavailable — transient."""

    def __init__(self, message: str = "reference unavailable"):
        super().__init__(UNAVAILABLE_REFERENCE, message, retryable=True)


class InvalidVariantIdentity(ReanalysisError):
    """The variant identity cannot be resolved — permanent (deterministic)."""

    def __init__(self, message: str = "invalid variant identity"):
        super().__init__(INVALID_VARIANT_IDENTITY, message, retryable=False)


class SkipReanalysis(Exception):
    """Raised by a resolver/runner to intentionally skip a variant (not a failure)."""

    def __init__(self, reason_code: str = NO_EVIDENCE, message: str = ""):
        super().__init__(message or reason_code)
        self.reason_code = reason_code


# --------------------------------------------------------------------------- #
# Trigger detection (pure)                                                     #
# --------------------------------------------------------------------------- #
def provider_version_changes(previous: Dict[str, str],
                             current: Dict[str, str]) -> Dict[str, Tuple[Optional[str], str]]:
    """Return ``{source: (old_version, new_version)}`` for changed/added providers.

    A provider present in ``current`` whose version differs from ``previous`` (or is
    new) is a reanalysis trigger; an unchanged or removed provider is not.
    """
    changes: Dict[str, Tuple[Optional[str], str]] = {}
    for source, new_version in current.items():
        old_version = previous.get(source)
        if old_version != new_version:
            changes[source] = (old_version, new_version)
    return changes


def config_version_changed(previous: Optional[str],
                           current: str = C.ENGINE_VERSION) -> bool:
    """True iff the engine/config version differs from the last-seen one."""
    return previous != current


def variant_id_of(item: Any) -> str:
    """Extract a variant id from a QueueItem, a DB row dict, or a bare string."""
    if isinstance(item, q.QueueItem):
        return item.variant_id
    if isinstance(item, dict):
        return str(item["variant_id"])
    return str(item)


def trigger_of(item: Any, default: str = "evidence") -> str:
    if isinstance(item, q.QueueItem):
        return item.trigger
    if isinstance(item, dict):
        return item.get("trigger", default)
    return default


# --------------------------------------------------------------------------- #
# The run loop (pure / backend-agnostic)                                       #
# --------------------------------------------------------------------------- #
def execute_run(
    items: Iterable[Any],
    *,
    resolve_events: Callable[[Any], List[Any]],
    run_one: Callable[[Any, List[Any]], Any],
    report: Optional[RunReport] = None,
    trigger: str = "mixed",
    max_attempts: int = 1,
    on_outcome: Optional[Callable[[Any, VariantOutcome], None]] = None,
) -> RunReport:
    """Drive a reanalysis pass over ``items`` and return the filled :class:`RunReport`.

    For each item: ``resolve_events(item)`` produces the current evidence, then
    ``run_one(item, events)`` performs the reanalysis (typically a thin wrapper over
    ``monitoring.reanalysis.reanalyze``) and returns a ``ReanalysisResult``-shaped
    object. Either callable may raise :class:`SkipReanalysis` (recorded as *skipped*)
    or :class:`ReanalysisError` (retried while ``retryable`` and attempts remain,
    else recorded as *failed* with its reason code). ``on_outcome`` (optional) is
    invoked with each variant's :class:`VariantOutcome` — used by the DB backend to
    move the queue item to its terminal state.
    """
    report = report or RunReport(trigger=trigger)
    for item in items:
        vid = variant_id_of(item)
        outcome = _run_item(item, vid, resolve_events, run_one, report, max_attempts)
        if on_outcome is not None:
            on_outcome(item, outcome)
    report.finish()
    return report


def _run_item(item, vid, resolve_events, run_one, report, max_attempts) -> VariantOutcome:
    attempt = 0
    while True:
        attempt += 1
        try:
            events = resolve_events(item)
            result = run_one(item, events)
        except SkipReanalysis as skip:
            return report.record_skip(vid, skip.reason_code, str(skip))
        except ReanalysisError as err:
            if err.retryable and attempt < max_attempts:
                continue
            return report.record_failure(vid, err.reason_code, str(err))
        return report.record_result(vid, result)


# --------------------------------------------------------------------------- #
# DB orchestration (clinical.reanalysis_queue + clinical.reanalysis_run)        #
# --------------------------------------------------------------------------- #
_OUTCOME_TO_QUEUE_STATE = {
    rr.UNCHANGED: "done",
    rr.SAME_TIER: "done",
    rr.CROSSED: "done",
    rr.FAILED: "failed",
    rr.SKIPPED: "skipped",
}


def run_from_queue(
    cur,
    *,
    tenant_id: str,
    resolve_events: Callable[[Dict[str, Any]], List[Any]],
    trigger: Optional[str] = None,
    limit: Optional[int] = None,
    max_attempts: int = 2,
    engine_version: str = C.ENGINE_VERSION,
    persist: bool = True,
) -> RunReport:
    """Claim pending ``clinical.reanalysis_queue`` items, reanalyze, and report.

    Opens a ``clinical.reanalysis_run`` row, atomically claims pending items (marking
    them ``running`` and stamping the ``run_id``), reanalyzes each via
    ``monitoring.reanalysis.reanalyze``, moves every queue item to its terminal state
    (done / failed / skipped) with a deterministic reason, and finalizes the run
    report. ``cur`` must be a tenant-scoped session.
    """
    from monitoring import reanalysis as rean  # lazy: keep ops importable without it

    report = RunReport(trigger=trigger or "mixed")
    run_id = rr.start_run(cur, tenant_id=tenant_id, trigger=report.trigger) if persist else None
    items = q.claim_batch(cur, trigger=trigger, limit=limit, run_id=run_id)

    def run_one(item: Dict[str, Any], events: List[Any]):
        return rean.reanalyze(
            cur, tenant_id=tenant_id, variant_id=str(item["variant_id"]),
            new_events=events, engine_version=engine_version,
            trigger=item.get("trigger", "evidence"), persist=persist,
        )

    def on_outcome(item: Dict[str, Any], outcome: VariantOutcome) -> None:
        q.mark(
            cur, str(item["queue_id"]),
            state=_OUTCOME_TO_QUEUE_STATE[outcome.outcome],
            error=outcome.message, reason_code=outcome.reason_code,
        )

    execute_run(
        items, resolve_events=resolve_events, run_one=run_one,
        report=report, max_attempts=max_attempts, on_outcome=on_outcome,
    )
    if persist:
        rr.finalize_run(cur, run_id, report)
    return report
