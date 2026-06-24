"""Case model, status pipeline, SLA clock, and the worklist store.

A :class:`Case` is one ordered specimen under review. It carries the operational
identity a lab queue needs — an ``accession`` (the human-facing order id), a
``specimen``, an ``ordering_provider`` — plus a small, access-controlled PHI
context (``patient_mrn``/``patient_name``/``indication``). It links to the
de-identified classification receipts (``classification_ids``) produced for the
variant(s) on the order, so the case is the join between the daily clinical
workflow and the research-domain engine output, without duplicating either.

Status is a small state machine (:data:`ALLOWED_TRANSITIONS`). Transitions are the
only way ``status`` changes, every transition is recorded on the case ``history``
with its actor, and the pipeline timestamps (``signed_at``/``released_at``) are
stamped as the case crosses them — so turnaround and SLA are computable from the
record rather than guessed.

Two stores implement one interface, mirroring ``api.store`` and
``evidence.workbench``:

  * :class:`InMemoryWorklistStore` — dependency-free, tenant-partitioned, used by
    the test suite and any no-DB environment, and the API's default fallback.
  * :class:`DbWorklistStore` — PostgreSQL-backed, delegating to the additive
    ``storage.worklist`` repository inside a tenant-scoped (RLS) session;
    ``storage.worklist`` imports psycopg lazily so importing this module never
    requires the database driver.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

# --------------------------------------------------------------------------- #
# Vocabulary                                                                  #
# --------------------------------------------------------------------------- #
#: Case lifecycle states. The primary pipeline is draft -> in_review -> signed ->
#: released; on_hold / cancelled are side states reachable from the pipeline.
STATUSES = ("draft", "in_review", "signed", "released", "on_hold", "cancelled")

#: Allowed status transitions. A transition not listed here is rejected — the
#: pipeline can only move forward, kick back one step, pause, or cancel.
ALLOWED_TRANSITIONS: Dict[str, frozenset] = {
    "draft": frozenset({"in_review", "on_hold", "cancelled"}),
    "in_review": frozenset({"signed", "draft", "on_hold", "cancelled"}),
    # signed -> in_review is a kickback (e.g. an error caught before release).
    "signed": frozenset({"released", "in_review", "on_hold"}),
    # released -> in_review reopens for an amendment / reanalysis-driven change.
    "released": frozenset({"in_review"}),
    "on_hold": frozenset({"draft", "in_review", "cancelled"}),
    "cancelled": frozenset(),  # terminal
}

#: Turnaround priorities, fastest first.
PRIORITIES = ("stat", "urgent", "routine")

#: SLA turnaround target (hours) per priority. Used to derive ``due_at`` from
#: ``received_at`` when a due date is not supplied explicitly. These are sensible
#: defaults, not a clinical commitment — a tenant SLA policy can override them.
SLA_TARGET_HOURS: Dict[str, int] = {"stat": 24, "urgent": 72, "routine": 336}

#: How close (hours) to ``due_at`` an open case is flagged ``due_soon``.
DUE_SOON_WINDOW_HOURS = 24

#: PHI-context fields redacted from de-identified views. The case exists in the
#: clinical domain, but these are the only fields that carry patient-identifying
#: or clinically sensitive content; everything else is operational metadata.
PHI_FIELDS = ("patient_mrn", "patient_name", "indication")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_ts(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


class CaseError(ValueError):
    """Raised when a case is malformed or a transition is not allowed."""


# --------------------------------------------------------------------------- #
# Domain model                                                                #
# --------------------------------------------------------------------------- #
@dataclass
class Case:
    """One ordered specimen on the worklist (tenant-scoped, lightly PHI-bearing).

    Required identity is the ``accession`` (the lab's order number). ``priority``
    and ``status`` drive the queue; the ``patient_*`` / ``indication`` fields are
    the access-controlled PHI context. ``classification_ids`` links the case to
    the de-identified engine receipts for its variant(s).
    """

    accession: str
    tenant_id: str
    status: str = "draft"
    priority: str = "routine"
    assigned_to: Optional[str] = None
    # Operational (non-PHI) order context.
    specimen_id: Optional[str] = None
    specimen_type: Optional[str] = None
    ordering_provider: Optional[str] = None
    ordering_facility: Optional[str] = None
    test_code: Optional[str] = None
    # PHI context (redacted from de-identified views; see PHI_FIELDS).
    patient_mrn: Optional[str] = None
    patient_name: Optional[str] = None
    indication: Optional[str] = None
    # Turnaround clock.
    received_at: Optional[str] = None
    due_at: Optional[str] = None
    notes: Optional[str] = None
    classification_ids: List[str] = field(default_factory=list)
    # Assigned/stamped by the store.
    case_id: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    signed_at: Optional[str] = None
    released_at: Optional[str] = None
    history: List[Dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.accession = str(self.accession or "").strip()
        if not self.accession:
            raise CaseError("a case requires an accession (order id)")
        if not self.tenant_id:
            raise CaseError("a case requires a tenant_id")
        if self.status not in STATUSES:
            raise CaseError(f"status must be one of {STATUSES}, got {self.status!r}")
        self.priority = str(self.priority or "routine").lower()
        if self.priority not in PRIORITIES:
            raise CaseError(f"priority must be one of {PRIORITIES}, got {self.priority!r}")
        for ts_field in ("received_at", "due_at"):
            value = getattr(self, ts_field)
            if value is not None and _parse_ts(value) is None:
                raise CaseError(f"{ts_field} must be an ISO-8601 timestamp, got {value!r}")
        # Derive a due date from the SLA target when one was not supplied.
        if self.due_at is None and self.received_at is not None:
            received = _parse_ts(self.received_at)
            if received is not None:
                target = SLA_TARGET_HOURS.get(self.priority, SLA_TARGET_HOURS["routine"])
                self.due_at = (received + timedelta(hours=target)).isoformat()
        # Normalise + de-duplicate links, preserving order.
        seen: set = set()
        deduped: List[str] = []
        for cid in self.classification_ids:
            cid = str(cid)
            if cid not in seen:
                seen.add(cid)
                deduped.append(cid)
        self.classification_ids = deduped

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Case":
        fields = {
            "accession", "tenant_id", "status", "priority", "assigned_to",
            "specimen_id", "specimen_type", "ordering_provider", "ordering_facility",
            "test_code", "patient_mrn", "patient_name", "indication", "received_at",
            "due_at", "notes", "classification_ids", "case_id", "created_at",
            "updated_at", "signed_at", "released_at", "history",
        }
        return cls(**{k: d[k] for k in fields if k in d})


def _open_status(status: str) -> bool:
    """A case is *open* (its turnaround clock still runs) until released/cancelled."""
    return status not in ("released", "cancelled")


def sla_view(row: Dict[str, Any], *, as_of: Optional[datetime] = None) -> Dict[str, Any]:
    """Compute SLA + turnaround indicators for one case row.

    Returns ``sla_status`` (one of ``none``/``on_track``/``due_soon``/``overdue``/
    ``released``/``cancelled``), ``turnaround_hours`` (received -> completion or
    now), and ``hours_to_due`` (negative when overdue). Deterministic given
    ``as_of`` so the queue is testable.
    """
    now = as_of or _now()
    status = row.get("status", "draft")
    due = _parse_ts(row.get("due_at"))
    start = _parse_ts(row.get("received_at")) or _parse_ts(row.get("created_at"))
    end = _parse_ts(row.get("released_at")) if status == "released" else now

    turnaround_hours: Optional[float] = None
    if start is not None and end is not None:
        turnaround_hours = round((end - start).total_seconds() / 3600.0, 2)

    hours_to_due: Optional[float] = None
    if due is not None:
        hours_to_due = round((due - now).total_seconds() / 3600.0, 2)

    if status == "released":
        sla_status = "released"
    elif status == "cancelled":
        sla_status = "cancelled"
    elif due is None:
        sla_status = "none"
    elif now > due:
        sla_status = "overdue"
    elif (due - now) <= timedelta(hours=DUE_SOON_WINDOW_HOURS):
        sla_status = "due_soon"
    else:
        sla_status = "on_track"

    return {
        "sla_status": sla_status,
        "turnaround_hours": turnaround_hours,
        "hours_to_due": hours_to_due,
    }


def redact_phi(row: Dict[str, Any]) -> Dict[str, Any]:
    """Return a copy of ``row`` with PHI-context fields nulled out.

    The default boundary for the worklist: list views and the standard detail view
    are de-identified. ``phi_redacted`` flags that PHI is present but withheld, so
    a UI can show a "request PHI" affordance rather than implying the field is empty.
    """
    out = dict(row)
    had_phi = any(out.get(f) is not None for f in PHI_FIELDS)
    for f in PHI_FIELDS:
        out[f] = None
    out["phi_redacted"] = had_phi
    return out


def case_view(
    row: Dict[str, Any], *, include_phi: bool = False, as_of: Optional[datetime] = None
) -> Dict[str, Any]:
    """The read shape for a case: SLA indicators added, PHI redacted by default."""
    view = dict(row)
    view.update(sla_view(view, as_of=as_of))
    view["variant_count"] = len(view.get("classification_ids") or [])
    if include_phi:
        view["phi_redacted"] = False
        return view
    return redact_phi(view)


# --------------------------------------------------------------------------- #
# Store interface + in-memory double                                          #
# --------------------------------------------------------------------------- #
#: Sentinel distinguishing "field not supplied" from "set to None" in patches.
_UNSET: Any = object()


def _bulk_summary(results: List[Dict[str, Any]], **extra: Any) -> Dict[str, Any]:
    """Wrap per-case bulk results with a counts summary, preserving order."""
    succeeded = sum(1 for r in results if r.get("ok"))
    return {
        **extra,
        "summary": {
            "requested": len(results),
            "succeeded": succeeded,
            "failed": len(results) - succeeded,
        },
        "results": results,
    }


class WorklistStore:
    """Persistence interface for the case worklist. Every method is tenant-scoped."""

    def create_case(self, case: Case) -> Dict[str, Any]:
        raise NotImplementedError

    def get_case(
        self, *, tenant_id: str, case_id: str, include_phi: bool = False,
        as_of: Optional[datetime] = None,
    ) -> Optional[Dict[str, Any]]:
        raise NotImplementedError

    def list_cases(
        self, *, tenant_id: str, status: Optional[str] = None,
        assigned_to: Optional[str] = None, priority: Optional[str] = None,
        query: Optional[str] = None, sla_status: Optional[str] = None,
        unassigned: bool = False, as_of: Optional[datetime] = None,
    ) -> List[Dict[str, Any]]:
        raise NotImplementedError

    def update_case(
        self, *, tenant_id: str, case_id: str, assigned_to: Any = _UNSET,
        priority: Any = _UNSET, due_at: Any = _UNSET, notes: Any = _UNSET,
        actor: Optional[str] = None,
    ) -> Dict[str, Any]:
        raise NotImplementedError

    def transition_case(
        self, *, tenant_id: str, case_id: str, to_status: str,
        actor: Optional[str] = None, note: Optional[str] = None,
        as_of: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        raise NotImplementedError

    def attach_classification(
        self, *, tenant_id: str, case_id: str, classification_id: str,
        actor: Optional[str] = None,
    ) -> Dict[str, Any]:
        raise NotImplementedError

    # ----------------------------------------------------------------------- #
    # Bulk operations                                                         #
    # ----------------------------------------------------------------------- #
    # Both bulk methods are concrete on the base class (like ``metrics``): they
    # delegate to the single-case primitives, so every store implements them by
    # implementing the primitives. Each case is applied *independently* — a
    # rejected or missing case fails that item only and never aborts the batch,
    # so a multi-select over a mixed-status queue does as much as is legal and
    # reports the rest. Results preserve the requested order.

    def bulk_assign(
        self, *, tenant_id: str, case_ids: List[str],
        assigned_to: Optional[str], actor: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Assign (or, with ``assigned_to=None``, unassign) many cases at once."""
        results: List[Dict[str, Any]] = []
        for cid in case_ids:
            cid = str(cid)
            try:
                row = self.update_case(
                    tenant_id=tenant_id, case_id=cid,
                    assigned_to=assigned_to, actor=actor,
                )
                results.append({"case_id": cid, "ok": True,
                                "assigned_to": row.get("assigned_to")})
            except CaseError as exc:
                results.append({"case_id": cid, "ok": False,
                                "error": str(exc), "error_code": "rejected"})
            except LookupError:
                results.append({"case_id": cid, "ok": False,
                                "error": "case not found", "error_code": "not_found"})
        return _bulk_summary(results, assigned_to=assigned_to)

    def bulk_transition(
        self, *, tenant_id: str, case_ids: List[str], to_status: str,
        actor: Optional[str] = None, note: Optional[str] = None,
        as_of: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """Move many cases to ``to_status``; each is validated independently."""
        results: List[Dict[str, Any]] = []
        for cid in case_ids:
            cid = str(cid)
            try:
                row = self.transition_case(
                    tenant_id=tenant_id, case_id=cid, to_status=to_status,
                    actor=actor, note=note, as_of=as_of,
                )
                results.append({"case_id": cid, "ok": True, "status": row["status"]})
            except CaseError as exc:
                results.append({"case_id": cid, "ok": False,
                                "error": str(exc), "error_code": "rejected"})
            except LookupError:
                results.append({"case_id": cid, "ok": False,
                                "error": "case not found", "error_code": "not_found"})
        return _bulk_summary(results, to_status=to_status)

    def metrics(
        self, *, tenant_id: str, as_of: Optional[datetime] = None
    ) -> Dict[str, Any]:
        """Queue summary: totals by status / priority / SLA, unassigned + overdue."""
        rows = self.list_cases(tenant_id=tenant_id, as_of=as_of)
        by_status = {s: 0 for s in STATUSES}
        by_priority = {p: 0 for p in PRIORITIES}
        by_sla: Dict[str, int] = {}
        unassigned = overdue = due_soon = 0
        open_total = 0
        for row in rows:
            by_status[row["status"]] = by_status.get(row["status"], 0) + 1
            by_priority[row["priority"]] = by_priority.get(row["priority"], 0) + 1
            sla = row.get("sla_status", "none")
            by_sla[sla] = by_sla.get(sla, 0) + 1
            if _open_status(row["status"]):
                open_total += 1
                if not row.get("assigned_to"):
                    unassigned += 1
            if sla == "overdue":
                overdue += 1
            elif sla == "due_soon":
                due_soon += 1
        return {
            "total": len(rows),
            "open": open_total,
            "by_status": by_status,
            "by_priority": by_priority,
            "by_sla": by_sla,
            "unassigned": unassigned,
            "overdue": overdue,
            "due_soon": due_soon,
        }


def _validate_transition(current: str, to_status: str) -> None:
    if to_status not in STATUSES:
        raise CaseError(f"status must be one of {STATUSES}, got {to_status!r}")
    if to_status == current:
        raise CaseError(f"case is already {current!r}")
    allowed = ALLOWED_TRANSITIONS.get(current, frozenset())
    if to_status not in allowed:
        raise CaseError(
            f"illegal transition {current!r} -> {to_status!r}; "
            f"allowed from {current!r}: {sorted(allowed)}"
        )


class InMemoryWorklistStore(WorklistStore):
    """Dependency-free, tenant-partitioned worklist store (the analogue of RLS)."""

    def __init__(self) -> None:
        # tenant_id -> {case_id: full row}
        self._cases: Dict[str, Dict[str, Dict[str, Any]]] = {}

    def _tenant(self, tenant_id: str) -> Dict[str, Dict[str, Any]]:
        return self._cases.setdefault(tenant_id, {})

    def create_case(self, case: Case) -> Dict[str, Any]:
        cid = case.case_id or str(uuid.uuid4())
        case.case_id = cid
        now = _now().isoformat()
        case.created_at = case.created_at or now
        case.updated_at = now
        if not case.history:
            case.history = [{
                "from": None, "to": case.status, "at": now,
                "actor": None, "note": "created",
            }]
        row = case.to_dict()
        self._tenant(case.tenant_id)[cid] = row
        return case_view(row, include_phi=True)

    def _row(self, tenant_id: str, case_id: str) -> Dict[str, Any]:
        row = self._tenant(tenant_id).get(case_id)
        if row is None:
            raise LookupError(f"case {case_id} not visible to this tenant")
        return row

    def get_case(self, *, tenant_id, case_id, include_phi=False, as_of=None):
        row = self._tenant(tenant_id).get(case_id)
        if row is None:
            return None
        return case_view(row, include_phi=include_phi, as_of=as_of)

    def list_cases(
        self, *, tenant_id, status=None, assigned_to=None, priority=None,
        query=None, sla_status=None, unassigned=False, as_of=None,
    ) -> List[Dict[str, Any]]:
        rows = [case_view(r, include_phi=False, as_of=as_of)
                for r in self._tenant(tenant_id).values()]
        if status is not None:
            rows = [r for r in rows if r["status"] == status]
        if assigned_to is not None:
            rows = [r for r in rows if r.get("assigned_to") == assigned_to]
        if priority is not None:
            rows = [r for r in rows if r["priority"] == priority]
        if sla_status is not None:
            rows = [r for r in rows if r.get("sla_status") == sla_status]
        if unassigned:
            rows = [r for r in rows if not r.get("assigned_to")]
        if query:
            q = query.strip().lower()
            # Search operational (non-PHI) fields only — PHI is never an index here.
            def _match(r: Dict[str, Any]) -> bool:
                for fname in ("accession", "specimen_id", "ordering_provider",
                              "ordering_facility", "test_code"):
                    val = r.get(fname)
                    if val and q in str(val).lower():
                        return True
                return False
            rows = [r for r in rows if _match(r)]
        # Worklist order: overdue first, then by due date, then newest.
        sla_rank = {"overdue": 0, "due_soon": 1, "on_track": 2, "none": 3,
                    "released": 4, "cancelled": 5}
        rows.sort(key=lambda r: (
            sla_rank.get(str(r.get("sla_status")), 9),
            r.get("due_at") or "9999",
            r.get("created_at") or "",
        ))
        return rows

    def update_case(
        self, *, tenant_id, case_id, assigned_to=_UNSET, priority=_UNSET,
        due_at=_UNSET, notes=_UNSET, actor=None,
    ) -> Dict[str, Any]:
        row = self._row(tenant_id, case_id)
        if assigned_to is not _UNSET:
            row["assigned_to"] = assigned_to
        if priority is not _UNSET:
            p = str(priority or "").lower()
            if p not in PRIORITIES:
                raise CaseError(f"priority must be one of {PRIORITIES}, got {priority!r}")
            row["priority"] = p
        if due_at is not _UNSET:
            if due_at is not None and _parse_ts(due_at) is None:
                raise CaseError(f"due_at must be an ISO-8601 timestamp, got {due_at!r}")
            row["due_at"] = due_at
        if notes is not _UNSET:
            row["notes"] = notes
        row["updated_at"] = _now().isoformat()
        return case_view(row, include_phi=True)

    def transition_case(
        self, *, tenant_id, case_id, to_status, actor=None, note=None, as_of=None,
    ) -> Dict[str, Any]:
        row = self._row(tenant_id, case_id)
        current = row["status"]
        _validate_transition(current, to_status)
        when = (as_of or _now())
        when_iso = when.isoformat()
        row["status"] = to_status
        row["updated_at"] = when_iso
        if to_status == "signed" and not row.get("signed_at"):
            row["signed_at"] = when_iso
        if to_status == "released":
            row["released_at"] = when_iso
        row.setdefault("history", []).append({
            "from": current, "to": to_status, "at": when_iso,
            "actor": actor, "note": note,
        })
        return case_view(row, include_phi=True, as_of=as_of)

    def attach_classification(
        self, *, tenant_id, case_id, classification_id, actor=None,
    ) -> Dict[str, Any]:
        row = self._row(tenant_id, case_id)
        cid = str(classification_id)
        ids = row.setdefault("classification_ids", [])
        if cid not in ids:
            ids.append(cid)
            row["updated_at"] = _now().isoformat()
        return case_view(row, include_phi=True)


# --------------------------------------------------------------------------- #
# PostgreSQL-backed store                                                     #
# --------------------------------------------------------------------------- #
class DbWorklistStore(WorklistStore):
    """PostgreSQL-backed worklist store (clinical schema, RLS-isolated).

    Delegates to the additive ``storage.worklist`` repository inside a
    tenant-scoped session, exactly like ``DbWorkbenchStore`` delegates to
    ``storage.evidence``. ``storage.worklist`` imports psycopg at use, so it is
    imported lazily here — importing this module never requires the driver.
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

        return connect(self._db_name)

    def _tenant_session(self, conn, tenant_id: str):
        from storage.db import tenant_session

        return tenant_session(conn, tenant_id, role=self._role)

    def create_case(self, case: Case) -> Dict[str, Any]:
        from storage import worklist as repo

        with self._conn() as conn:
            with self._tenant_session(conn, case.tenant_id) as cur:
                return repo.insert_case(cur, case)

    def get_case(self, *, tenant_id, case_id, include_phi=False, as_of=None):
        from storage import worklist as repo

        with self._conn() as conn:
            with self._tenant_session(conn, tenant_id) as cur:
                row = repo.get_case(cur, case_id)
        if row is None:
            return None
        return case_view(row, include_phi=include_phi, as_of=as_of)

    def list_cases(
        self, *, tenant_id, status=None, assigned_to=None, priority=None,
        query=None, sla_status=None, unassigned=False, as_of=None,
    ) -> List[Dict[str, Any]]:
        from storage import worklist as repo

        with self._conn() as conn:
            with self._tenant_session(conn, tenant_id) as cur:
                rows = repo.list_cases(
                    cur, status=status, assigned_to=assigned_to, priority=priority,
                    query=query, unassigned=unassigned,
                )
        views = [case_view(r, include_phi=False, as_of=as_of) for r in rows]
        if sla_status is not None:
            views = [v for v in views if v.get("sla_status") == sla_status]
        return views

    def update_case(
        self, *, tenant_id, case_id, assigned_to=_UNSET, priority=_UNSET,
        due_at=_UNSET, notes=_UNSET, actor=None,
    ) -> Dict[str, Any]:
        from storage import worklist as repo

        patch: Dict[str, Any] = {}
        if assigned_to is not _UNSET:
            patch["assigned_to"] = assigned_to
        if priority is not _UNSET:
            p = str(priority or "").lower()
            if p not in PRIORITIES:
                raise CaseError(f"priority must be one of {PRIORITIES}, got {priority!r}")
            patch["priority"] = p
        if due_at is not _UNSET:
            if due_at is not None and _parse_ts(due_at) is None:
                raise CaseError(f"due_at must be an ISO-8601 timestamp, got {due_at!r}")
            patch["due_at"] = due_at
        if notes is not _UNSET:
            patch["notes"] = notes
        with self._conn() as conn:
            with self._tenant_session(conn, tenant_id) as cur:
                row = repo.update_case(cur, case_id, patch)
        return case_view(row, include_phi=True)

    def transition_case(
        self, *, tenant_id, case_id, to_status, actor=None, note=None, as_of=None,
    ) -> Dict[str, Any]:
        from storage import worklist as repo

        with self._conn() as conn:
            with self._tenant_session(conn, tenant_id) as cur:
                current = repo.get_case_status(cur, case_id)
                if current is None:
                    raise LookupError(f"case {case_id} not visible to this tenant")
                _validate_transition(current, to_status)
                row = repo.transition_case(
                    cur, case_id, current=current, to_status=to_status,
                    actor=actor, note=note,
                )
        return case_view(row, include_phi=True, as_of=as_of)

    def attach_classification(
        self, *, tenant_id, case_id, classification_id, actor=None,
    ) -> Dict[str, Any]:
        from storage import worklist as repo

        with self._conn() as conn:
            with self._tenant_session(conn, tenant_id) as cur:
                row = repo.attach_classification(cur, case_id, str(classification_id))
        return case_view(row, include_phi=True)
