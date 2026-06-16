"""Operational reanalysis run reports (gap §5 task 4).

A :class:`RunReport` is the per-run roll-up an operator reads after a continuous-
reanalysis pass: how many variants were *checked*, and of those how many were
*unchanged* (churn-free no-op), *same-tier* changed (audited, no page), *crossing*
(tier change -> alert), *failed* (errored with a deterministic reason), and
*skipped* (intentionally not reanalyzed, e.g. no evidence available).

The report is pure, stdlib-only data (no DB import at module load); the optional
``start_run`` / ``finalize_run`` / ``get_run`` helpers persist it to
``clinical.reanalysis_run`` when given an open, tenant-scoped cursor.

Invariant: ``checked == unchanged + same_tier + crossed + failed + skipped`` — the
same CHECK the schema enforces, so a persisted run can never silently drop a
variant.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# Outcome buckets (also the per-variant ``outcome`` values stored in ``detail``).
UNCHANGED = "unchanged"
SAME_TIER = "same_tier"
CROSSED = "crossed"
FAILED = "failed"
SKIPPED = "skipped"
OUTCOMES = (UNCHANGED, SAME_TIER, CROSSED, FAILED, SKIPPED)


@dataclass
class VariantOutcome:
    """One variant's outcome within a run (the unit stored in ``detail``)."""

    variant_id: str
    outcome: str  # one of OUTCOMES
    old_tier: Optional[str] = None
    new_tier: Optional[str] = None
    old_points: Optional[float] = None
    new_points: Optional[float] = None
    reason_code: Optional[str] = None  # deterministic code for failed/skipped
    message: Optional[str] = None
    reanalysis_id: Optional[str] = None
    alert_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "variant_id": str(self.variant_id),
            "outcome": self.outcome,
            "old_tier": self.old_tier,
            "new_tier": self.new_tier,
            "old_points": self.old_points,
            "new_points": self.new_points,
            "reason_code": self.reason_code,
            "message": self.message,
            "reanalysis_id": self.reanalysis_id,
            "alert_id": self.alert_id,
        }


@dataclass
class RunReport:
    """Accumulating roll-up of one operational reanalysis run."""

    trigger: str = "mixed"
    outcomes: List[VariantOutcome] = field(default_factory=list)
    started_at: _dt.datetime = field(default_factory=lambda: _dt.datetime.now(_dt.timezone.utc))
    finished_at: Optional[_dt.datetime] = None

    # ------------------------------------------------------------------ #
    # Recording                                                          #
    # ------------------------------------------------------------------ #
    def record_result(self, variant_id: str, result: Any) -> VariantOutcome:
        """Bucket a :class:`monitoring.reanalysis.ReanalysisResult`-shaped object.

        Accepts anything exposing ``changed`` / ``crossed`` / ``old_tier`` /
        ``new_tier`` (duck-typed, so ``ops`` need not import ``monitoring``):

          * ``changed is False``          -> unchanged (no churn)
          * ``changed and crossed``       -> crossed (a tier crossing alerted)
          * ``changed and not crossed``   -> same_tier (audited, no page)
        """
        if not getattr(result, "changed", False):
            outcome = UNCHANGED
        elif getattr(result, "crossed", False):
            outcome = CROSSED
        else:
            outcome = SAME_TIER
        vo = VariantOutcome(
            variant_id=str(variant_id),
            outcome=outcome,
            old_tier=getattr(result, "old_tier", None),
            new_tier=getattr(result, "new_tier", None),
            old_points=_as_float(getattr(result, "old_points", None)),
            new_points=_as_float(getattr(result, "new_points", None)),
            reanalysis_id=_as_str(getattr(result, "reanalysis_id", None)),
            alert_id=_as_str(getattr(result, "alert_id", None)),
        )
        self.outcomes.append(vo)
        return vo

    def record_failure(self, variant_id: str, reason_code: str,
                       message: Optional[str] = None) -> VariantOutcome:
        """Record a variant that errored, with a deterministic ``reason_code``."""
        vo = VariantOutcome(variant_id=str(variant_id), outcome=FAILED,
                            reason_code=reason_code, message=message)
        self.outcomes.append(vo)
        return vo

    def record_skip(self, variant_id: str, reason_code: str,
                    message: Optional[str] = None) -> VariantOutcome:
        """Record a variant intentionally not reanalyzed (e.g. no evidence)."""
        vo = VariantOutcome(variant_id=str(variant_id), outcome=SKIPPED,
                            reason_code=reason_code, message=message)
        self.outcomes.append(vo)
        return vo

    def finish(self) -> "RunReport":
        self.finished_at = _dt.datetime.now(_dt.timezone.utc)
        return self

    # ------------------------------------------------------------------ #
    # Derived counts                                                     #
    # ------------------------------------------------------------------ #
    def _count(self, outcome: str) -> int:
        return sum(1 for o in self.outcomes if o.outcome == outcome)

    @property
    def checked(self) -> int:
        return len(self.outcomes)

    @property
    def unchanged(self) -> int:
        return self._count(UNCHANGED)

    @property
    def same_tier(self) -> int:
        return self._count(SAME_TIER)

    @property
    def crossed(self) -> int:
        return self._count(CROSSED)

    @property
    def failed(self) -> int:
        return self._count(FAILED)

    @property
    def skipped(self) -> int:
        return self._count(SKIPPED)

    def counts(self) -> Dict[str, int]:
        return {
            "checked": self.checked,
            "unchanged": self.unchanged,
            "same_tier": self.same_tier,
            "crossed": self.crossed,
            "failed": self.failed,
            "skipped": self.skipped,
        }

    def failures(self) -> List[VariantOutcome]:
        return [o for o in self.outcomes if o.outcome == FAILED]

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"trigger": self.trigger}
        d.update(self.counts())
        d["detail"] = [o.to_dict() for o in self.outcomes]
        d["started_at"] = self.started_at.isoformat() if self.started_at else None
        d["finished_at"] = self.finished_at.isoformat() if self.finished_at else None
        return d

    def summary(self) -> str:
        c = self.counts()
        return (
            f"reanalysis run [{self.trigger}]: checked={c['checked']} "
            f"unchanged={c['unchanged']} same_tier={c['same_tier']} "
            f"crossed={c['crossed']} failed={c['failed']} skipped={c['skipped']}"
        )


def _as_float(value: Any) -> Optional[float]:
    return None if value is None else float(value)


def _as_str(value: Any) -> Optional[str]:
    return None if value is None else str(value)


# --------------------------------------------------------------------------- #
# Persistence (clinical.reanalysis_run); requires a tenant-scoped cursor       #
# --------------------------------------------------------------------------- #
def start_run(cur, *, tenant_id: str, trigger: str) -> str:
    """Open a ``clinical.reanalysis_run`` row and return its ``run_id``.

    Inserting up front gives queued items a ``run_id`` to reference while the run
    is in flight; :func:`finalize_run` writes the final counts + detail.
    """
    cur.execute(
        "INSERT INTO clinical.reanalysis_run (tenant_id, trigger) "
        "VALUES (%s, %s) RETURNING run_id",
        (tenant_id, trigger),
    )
    return str(cur.fetchone()["run_id"])


def finalize_run(cur, run_id: str, report: "RunReport") -> None:
    """Write the report's counts + per-variant detail onto an open run row."""
    from psycopg.types.json import Jsonb  # lazy: keep this module psycopg-free

    if report.finished_at is None:
        report.finish()
    c = report.counts()
    cur.execute(
        """
        UPDATE clinical.reanalysis_run
           SET checked = %s, unchanged = %s, same_tier = %s, crossed = %s,
               failed = %s, skipped = %s, detail = %s, finished_at = now()
         WHERE run_id = %s
        """,
        (
            c["checked"], c["unchanged"], c["same_tier"], c["crossed"],
            c["failed"], c["skipped"],
            Jsonb([o.to_dict() for o in report.outcomes]),
            run_id,
        ),
    )


def persist_run(cur, *, tenant_id: str, report: "RunReport") -> str:
    """Convenience: open + finalize a run row in one call; returns ``run_id``."""
    run_id = start_run(cur, tenant_id=tenant_id, trigger=report.trigger)
    finalize_run(cur, run_id, report)
    return run_id


def get_run(cur, run_id: str) -> Optional[Dict[str, Any]]:
    cur.execute("SELECT * FROM clinical.reanalysis_run WHERE run_id = %s", (run_id,))
    return cur.fetchone()


def list_runs(cur) -> List[Dict[str, Any]]:
    cur.execute("SELECT * FROM clinical.reanalysis_run ORDER BY started_at")
    return cur.fetchall()
