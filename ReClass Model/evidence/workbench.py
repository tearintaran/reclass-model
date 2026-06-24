"""Evidence workbench: reviewer/pipeline-entered structured evidence (job1 task 1).

The validation failures are *missing-evidence* failures, not scoring failures: the
engine reproduces expert tiers well when fed complete evidence and poorly when fed
sparse public data. This module makes evidence entry a first-class, auditable
surface for the criteria no single public score encodes — ``PVS1``/LoF, ``PS3``/
``BS3`` functional assays, ``PM3`` phasing, ``PP1``/``BS4`` segregation, ``PP4``
phenotype, ``PS4`` cohort/case-control, and ``BA1``/``BS1`` benign-frequency review.

A reviewer (or a validated pipeline) submits a :class:`ReviewerEvidence`: a single
standardized criterion mapped to a direction and strength, carrying the provenance
an audit requires — ``source``/``source_version``, a content ``checksum``, the
``access_date`` it was read, the ``reviewer`` who entered it, and the
``expires_at`` / ``re_review_at`` metadata that drives periodic re-review. Like the
rest of the research domain, it is **de-identified**: it is keyed only on the public
``variant_key`` and carries no patient or tenant identifier.

``ReviewerEvidence.to_event()`` yields the engine ``EvidenceEvent`` the workbench
contributes, so reviewer-entered evidence sums into a tier exactly like any other
source. ``points`` stays ``None`` for strength-derived evidence so a stored
classification still reconstructs byte-for-byte.

Two stores implement the same persistence interface (mirroring ``api.store``):

  * :class:`InMemoryWorkbenchStore` — a dependency-free double used by the test
    suite and any no-DB environment, and the default the API falls back to.
  * :class:`DbWorkbenchStore`       — the PostgreSQL-backed store, delegating to the
    additive ``storage.evidence`` repositories (imported lazily so importing the
    workbench never requires the database driver).
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from engine.scoring import EvidenceEvent

from . import coverage as _coverage

#: The ACMG criteria the workbench is built to capture (job1 task 1). Entry is not
#: *restricted* to these — any criterion the engine knows is accepted — but these are
#: the gaps the workbench exists to close, surfaced to the UI and the coverage model.
WORKBENCH_CRITERIA: Dict[str, str] = {
    "PVS1": "Null / loss-of-function variant",
    "PS3": "Well-established functional assay — damaging",
    "BS3": "Well-established functional assay — normal",
    "PM3": "In trans with a pathogenic variant (recessive)",
    "PP1": "Cosegregation with disease",
    "BS4": "Non-segregation with disease",
    "PP4": "Phenotype/HPO specific for the gene/disease",
    "PS4": "Increased prevalence in affected vs. controls",
    "BA1": "Stand-alone benign allele frequency",
    "BS1": "Allele frequency greater than expected",
}

_DIRECTIONS = ("pathogenic", "benign", "neutral")
_STATUSES = ("active", "expired", "superseded", "withdrawn")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def evidence_checksum(record: Dict[str, Any]) -> str:
    """Stable SHA-256 over a reviewer-entered record's content (sorted, compact)."""
    payload = json.dumps(record, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class WorkbenchError(ValueError):
    """Raised when reviewer-entered evidence is malformed (rejected, never guessed)."""


@dataclass
class ReviewerEvidence:
    """One reviewer/pipeline-entered structured evidence record (de-identified).

    ``variant_key`` is the public coordinate the evidence is about; ``reviewer`` is
    the curator's identity (provenance, not patient PHI). ``points`` is left ``None``
    for strength-derived evidence so the reconstruction hash is unaffected.
    """

    variant_key: str
    acmg_criterion: str
    evidence_direction: str
    reviewer: str
    applied_strength: Optional[str] = None
    points: Optional[float] = None
    source: str = "reviewer"
    source_version: Optional[str] = None
    source_url: Optional[str] = None
    access_date: Optional[str] = None
    reviewer_credential: Optional[str] = None
    status: str = "active"
    notes: Optional[str] = None
    expires_at: Optional[str] = None
    re_review_at: Optional[str] = None
    #: Free-form structured payload the checksum is computed over (assay readout,
    #: cohort 2x2, segregation meioses, etc.). Kept out of the engine hash.
    record: Dict[str, Any] = field(default_factory=dict)
    #: Assigned by the store on persist.
    reviewer_evidence_id: Optional[str] = None
    checksum: Optional[str] = None
    checksum_algorithm: str = "sha256"
    entered_at: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.variant_key:
            raise WorkbenchError("reviewer evidence requires a variant_key")
        if not self.acmg_criterion:
            raise WorkbenchError("reviewer evidence requires an acmg_criterion")
        self.acmg_criterion = str(self.acmg_criterion).upper()
        if self.evidence_direction not in _DIRECTIONS:
            raise WorkbenchError(
                f"evidence_direction must be one of {_DIRECTIONS}, got {self.evidence_direction!r}"
            )
        if self.points is None and self.applied_strength is None \
                and self.evidence_direction != "neutral":
            raise WorkbenchError(
                "reviewer evidence needs an applied_strength or explicit points"
            )
        if not self.reviewer:
            raise WorkbenchError("reviewer evidence requires a reviewer identity")
        if self.status not in _STATUSES:
            raise WorkbenchError(f"status must be one of {_STATUSES}, got {self.status!r}")
        if self.checksum is None:
            self.checksum = evidence_checksum(self._checksum_payload())

    def _checksum_payload(self) -> Dict[str, Any]:
        """The content the checksum pins: the criterion mapping + entered record."""
        return {
            "variant_key": self.variant_key,
            "acmg_criterion": self.acmg_criterion,
            "evidence_direction": self.evidence_direction,
            "applied_strength": self.applied_strength,
            "points": self.points,
            "source": self.source,
            "source_version": self.source_version,
            "record": self.record,
        }

    @property
    def is_expired(self) -> bool:
        """True when ``status`` is expired (re-review lapsed)."""
        return self.status == "expired"

    def to_event(self) -> EvidenceEvent:
        """The engine ``EvidenceEvent`` this reviewer evidence contributes.

        Provenance (reviewer/checksum/access date) is carried on ``raw`` — outside
        the engine reconstruction hash — so attribution never perturbs a tier.
        """
        raw = dict(self.record)
        raw["provenance"] = {
            "reviewer": self.reviewer,
            "reviewer_credential": self.reviewer_credential,
            "source": self.source,
            "source_version": self.source_version,
            "source_url": self.source_url,
            "access_date": self.access_date,
            "checksum": self.checksum,
            "checksum_algorithm": self.checksum_algorithm,
            "status": self.status,
        }
        return EvidenceEvent(
            source=self.source,
            acmg_criterion=self.acmg_criterion,
            evidence_direction=self.evidence_direction,
            applied_strength=self.applied_strength,
            points=self.points,
            source_version=self.source_version,
            raw=raw,
        )

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["is_expired"] = self.is_expired
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ReviewerEvidence":
        fields = {
            "variant_key", "acmg_criterion", "evidence_direction", "reviewer",
            "applied_strength", "points", "source", "source_version", "source_url",
            "access_date", "reviewer_credential", "status", "notes", "expires_at",
            "re_review_at", "record", "reviewer_evidence_id", "checksum",
            "checksum_algorithm", "entered_at",
        }
        return cls(**{k: d[k] for k in fields if k in d})


# --------------------------------------------------------------------------- #
# Store interface + in-memory double                                          #
# --------------------------------------------------------------------------- #
class WorkbenchStore:
    """Persistence interface for reviewer-entered evidence. See module docstring."""

    def add_evidence(self, evidence: ReviewerEvidence) -> Dict[str, Any]:
        raise NotImplementedError

    def list_evidence(
        self, *, variant_key: Optional[str] = None, status: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        raise NotImplementedError

    def get_evidence(self, reviewer_evidence_id: str) -> Optional[Dict[str, Any]]:
        raise NotImplementedError

    def set_status(self, reviewer_evidence_id: str, status: str) -> Dict[str, Any]:
        raise NotImplementedError

    def expire_due(self, *, as_of: Optional[str] = None) -> List[str]:
        """Flip ``active`` entries past their ``expires_at`` to ``expired``.

        Returns the ids transitioned. ``as_of`` (ISO timestamp) defaults to now; it
        is explicit so the operation stays deterministic and testable.
        """
        raise NotImplementedError

    # -- coverage (tenant-scoped) ------------------------------------------- #
    def upsert_coverage(self, *, tenant_id: str, record: Any) -> Dict[str, Any]:
        raise NotImplementedError

    def list_coverage(self, *, tenant_id: str) -> List[Dict[str, Any]]:
        raise NotImplementedError

    def coverage_summary(self, *, tenant_id: str) -> Dict[str, Any]:
        """Overall + per-dimension blocked-case breakdown for a tenant's coverage."""
        return _coverage.summarize(self.list_coverage(tenant_id=tenant_id))

    # -- curation queue (tenant-scoped) ------------------------------------- #
    def enqueue_curation(self, *, tenant_id: str, item: Any) -> Optional[Dict[str, Any]]:
        raise NotImplementedError

    def list_curation(
        self, *, tenant_id: str, kind: Optional[str] = None, state: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        raise NotImplementedError

    def set_curation_state(
        self, *, tenant_id: str, curation_id: str, state: str
    ) -> Dict[str, Any]:
        raise NotImplementedError


def _parse_ts(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


#: Curation queue lifecycle states (mirrors clinical.curation_queue.state CHECK).
CURATION_STATES = ("open", "in_review", "resolved", "dismissed")
_CURATION_TERMINAL = {"resolved", "dismissed"}


class InMemoryWorkbenchStore(WorkbenchStore):
    """Dependency-free workbench store for tests and no-DB environments.

    Reviewer evidence is de-identified (no tenant). Coverage and curation are
    tenant-partitioned (the analogue of RLS), so one tenant never sees another's.
    """

    def __init__(self) -> None:
        self._rows: Dict[str, Dict[str, Any]] = {}
        # tenant_id -> {variant_key: coverage row}
        self._coverage: Dict[str, Dict[str, Dict[str, Any]]] = {}
        # tenant_id -> [curation row]
        self._curation: Dict[str, List[Dict[str, Any]]] = {}

    def add_evidence(self, evidence: ReviewerEvidence) -> Dict[str, Any]:
        rid = evidence.reviewer_evidence_id or str(uuid.uuid4())
        evidence.reviewer_evidence_id = rid
        if evidence.entered_at is None:
            evidence.entered_at = _now().isoformat()
        row = evidence.to_dict()
        self._rows[rid] = row
        return dict(row)

    def list_evidence(self, *, variant_key=None, status=None) -> List[Dict[str, Any]]:
        rows = list(self._rows.values())
        if variant_key is not None:
            rows = [r for r in rows if r["variant_key"] == variant_key]
        if status is not None:
            rows = [r for r in rows if r["status"] == status]
        rows.sort(key=lambda r: (r["variant_key"], r.get("entered_at") or "", r["reviewer_evidence_id"]))
        return [dict(r) for r in rows]

    def get_evidence(self, reviewer_evidence_id: str) -> Optional[Dict[str, Any]]:
        row = self._rows.get(reviewer_evidence_id)
        return dict(row) if row is not None else None

    def set_status(self, reviewer_evidence_id: str, status: str) -> Dict[str, Any]:
        if status not in _STATUSES:
            raise WorkbenchError(f"status must be one of {_STATUSES}, got {status!r}")
        row = self._rows.get(reviewer_evidence_id)
        if row is None:
            raise LookupError(f"reviewer evidence {reviewer_evidence_id} not found")
        row["status"] = status
        row["is_expired"] = status == "expired"
        return dict(row)

    def expire_due(self, *, as_of: Optional[str] = None) -> List[str]:
        cutoff = _parse_ts(as_of) or _now()
        flipped: List[str] = []
        for rid, row in self._rows.items():
            if row["status"] != "active":
                continue
            exp = _parse_ts(row.get("expires_at"))
            if exp is not None and exp <= cutoff:
                row["status"] = "expired"
                row["is_expired"] = True
                flipped.append(rid)
        return flipped

    # -- coverage ----------------------------------------------------------- #
    def upsert_coverage(self, *, tenant_id: str, record: Any) -> Dict[str, Any]:
        row = record.to_dict() if hasattr(record, "to_dict") else dict(record)
        row.setdefault("tenant_id", tenant_id)
        row["updated_at"] = _now().isoformat()
        vk = row.get("variant_key")
        if not vk:
            raise WorkbenchError("coverage record requires a variant_key")
        tenant_cov = self._coverage.setdefault(tenant_id, {})
        existing = tenant_cov.get(vk)
        row["coverage_id"] = existing["coverage_id"] if existing else str(uuid.uuid4())
        tenant_cov[vk] = row
        return dict(row)

    def list_coverage(self, *, tenant_id: str) -> List[Dict[str, Any]]:
        rows = list(self._coverage.get(tenant_id, {}).values())
        rows.sort(key=lambda r: r.get("variant_key") or "")
        return [dict(r) for r in rows]

    # -- curation ----------------------------------------------------------- #
    def enqueue_curation(self, *, tenant_id: str, item: Any) -> Optional[Dict[str, Any]]:
        view = item.to_dict() if hasattr(item, "to_dict") else dict(item)
        rows = self._curation.setdefault(tenant_id, [])
        # At most one OPEN (variant_key, kind): re-surfacing the same gap is a no-op.
        for existing in rows:
            if (existing["state"] == "open"
                    and existing["variant_key"] == view.get("variant_key")
                    and existing["kind"] == view.get("kind")):
                return None
        row = {
            "curation_id": str(uuid.uuid4()),
            "tenant_id": tenant_id,
            "variant_key": view.get("variant_key"),
            "kind": view.get("kind"),
            "severity": view.get("severity", "warning"),
            "detail": dict(view.get("detail") or {}),
            "state": "open",
            "created_at": _now().isoformat(),
            "resolved_at": None,
        }
        rows.append(row)
        return dict(row)

    def list_curation(self, *, tenant_id, kind=None, state=None) -> List[Dict[str, Any]]:
        rows = list(self._curation.get(tenant_id, []))
        if kind is not None:
            rows = [r for r in rows if r["kind"] == kind]
        if state is not None:
            rows = [r for r in rows if r["state"] == state]
        rows.sort(key=lambda r: r["created_at"], reverse=True)
        return [dict(r) for r in rows]

    def set_curation_state(self, *, tenant_id, curation_id, state) -> Dict[str, Any]:
        if state not in CURATION_STATES:
            raise WorkbenchError(f"state must be one of {CURATION_STATES}, got {state!r}")
        for row in self._curation.get(tenant_id, []):
            if row["curation_id"] == curation_id:
                row["state"] = state
                if state in _CURATION_TERMINAL:
                    row["resolved_at"] = _now().isoformat()
                return dict(row)
        raise LookupError(f"curation item {curation_id} not visible to this session")


class DbWorkbenchStore(WorkbenchStore):
    """PostgreSQL-backed reviewer-evidence store (research schema, de-identified).

    Delegates to the additive ``storage.evidence`` repositories. ``storage.evidence``
    imports psycopg at use, so it is imported lazily here — importing the workbench
    never requires the database driver.
    """

    def __init__(self, *, db_name: str = "reclass_dev", role: Optional[str] = None,
                 connect=None) -> None:
        self._db_name = db_name
        self._role = role
        self._connect = connect

    def _conn(self):
        if self._connect is not None:
            return self._connect()
        from storage.db import connect

        return connect(self._db_name, autocommit=True)

    def add_evidence(self, evidence: ReviewerEvidence) -> Dict[str, Any]:
        from storage import evidence as erepo

        with self._conn() as conn:
            with conn.cursor() as cur:
                row = erepo.insert_reviewer_evidence(cur, evidence)
        return row

    def list_evidence(self, *, variant_key=None, status=None) -> List[Dict[str, Any]]:
        from storage import evidence as erepo

        with self._conn() as conn:
            with conn.cursor() as cur:
                return erepo.list_reviewer_evidence(cur, variant_key=variant_key, status=status)

    def get_evidence(self, reviewer_evidence_id: str) -> Optional[Dict[str, Any]]:
        from storage import evidence as erepo

        with self._conn() as conn:
            with conn.cursor() as cur:
                return erepo.get_reviewer_evidence(cur, reviewer_evidence_id)

    def set_status(self, reviewer_evidence_id: str, status: str) -> Dict[str, Any]:
        from storage import evidence as erepo

        if status not in _STATUSES:
            raise WorkbenchError(f"status must be one of {_STATUSES}, got {status!r}")
        with self._conn() as conn:
            with conn.cursor() as cur:
                return erepo.set_reviewer_evidence_status(cur, reviewer_evidence_id, status)

    def expire_due(self, *, as_of: Optional[str] = None) -> List[str]:
        from storage import evidence as erepo

        with self._conn() as conn:
            with conn.cursor() as cur:
                return erepo.expire_reviewer_evidence(cur, as_of=as_of)

    # -- coverage + curation run inside a tenant-scoped (RLS) session -------- #
    def _tenant_session(self, conn, tenant_id: str):
        from storage.db import tenant_session

        return tenant_session(conn, tenant_id, role=self._role)

    def _tenant_conn(self):
        if self._connect is not None:
            return self._connect()
        from storage.db import connect

        return connect(self._db_name)

    def upsert_coverage(self, *, tenant_id: str, record: Any) -> Dict[str, Any]:
        from storage import evidence as erepo

        with self._tenant_conn() as conn:
            with self._tenant_session(conn, tenant_id) as cur:
                return erepo.upsert_coverage(cur, tenant_id=tenant_id, record=record)

    def list_coverage(self, *, tenant_id: str) -> List[Dict[str, Any]]:
        from storage import evidence as erepo

        with self._tenant_conn() as conn:
            with self._tenant_session(conn, tenant_id) as cur:
                return erepo.list_coverage(cur)

    def enqueue_curation(self, *, tenant_id: str, item: Any) -> Optional[Dict[str, Any]]:
        from storage import evidence as erepo

        with self._tenant_conn() as conn:
            with self._tenant_session(conn, tenant_id) as cur:
                return erepo.enqueue_curation_item(cur, tenant_id=tenant_id, item=item)

    def list_curation(self, *, tenant_id, kind=None, state=None) -> List[Dict[str, Any]]:
        from storage import evidence as erepo

        with self._tenant_conn() as conn:
            with self._tenant_session(conn, tenant_id) as cur:
                return erepo.list_curation_items(cur, kind=kind, state=state)

    def set_curation_state(self, *, tenant_id, curation_id, state) -> Dict[str, Any]:
        from storage import evidence as erepo

        with self._tenant_conn() as conn:
            with self._tenant_session(conn, tenant_id) as cur:
                return erepo.set_curation_state(cur, curation_id, state)
