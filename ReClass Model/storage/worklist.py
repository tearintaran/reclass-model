"""Case worklist repository (clinical schema, RLS-isolated).

Additive persistence for :mod:`worklist.case`, mirroring ``storage.evidence``:
plain functions over a cursor obtained from ``storage.db.tenant_session`` so every
statement runs under the tenant GUC and RLS isolates one tenant from another. The
case table lives in the ``clinical`` schema because, unlike reviewer evidence, a
case carries tenant-scoped (and lightly PHI-bearing) context.

``psycopg`` is imported at module load, so — like the other ``storage`` modules —
this is only imported lazily by ``worklist.case.DbWorklistStore``.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from psycopg.types.json import Jsonb

#: Columns returned by every case query, in a stable order. The names match
#: ``worklist.case.Case`` fields so ``case_view`` consumes a DB row unchanged.
_CASE_COLUMNS = (
    "case_id, tenant_id, accession, status, priority, assigned_to, specimen_id, "
    "specimen_type, ordering_provider, ordering_facility, test_code, patient_mrn, "
    "patient_name, indication, received_at, due_at, notes, classification_ids, "
    "signed_at, released_at, history, created_at, updated_at"
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _jsonify(value: Any) -> Any:
    """Coerce uuid/datetime values to JSON-friendly strings (recursively)."""
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        return [_jsonify(v) for v in value]
    if isinstance(value, dict):
        return {k: _jsonify(v) for k, v in value.items()}
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    return value


def _case_row(row: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    out = {k: _jsonify(v) for k, v in dict(row).items()}
    out.setdefault("classification_ids", [])
    out.setdefault("history", [])
    return out


def insert_case(cur, case: Any) -> Dict[str, Any]:
    """Persist a :class:`worklist.case.Case`; returns the stored row with its id."""
    history = list(case.history) or [{
        "from": None, "to": case.status, "at": _now_iso(),
        "actor": None, "note": "created",
    }]
    cur.execute(
        f"""
        INSERT INTO clinical.worklist_case (
            tenant_id, accession, status, priority, assigned_to, specimen_id,
            specimen_type, ordering_provider, ordering_facility, test_code,
            patient_mrn, patient_name, indication, received_at, due_at, notes,
            classification_ids, history
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING {_CASE_COLUMNS}
        """,
        (
            case.tenant_id, case.accession, case.status, case.priority,
            case.assigned_to, case.specimen_id, case.specimen_type,
            case.ordering_provider, case.ordering_facility, case.test_code,
            case.patient_mrn, case.patient_name, case.indication,
            case.received_at, case.due_at, case.notes,
            Jsonb(list(case.classification_ids or [])), Jsonb(history),
        ),
    )
    row = _case_row(cur.fetchone())
    assert row is not None
    return row


def get_case(cur, case_id: str) -> Optional[Dict[str, Any]]:
    cur.execute(
        f"SELECT {_CASE_COLUMNS} FROM clinical.worklist_case WHERE case_id = %s",
        (case_id,),
    )
    return _case_row(cur.fetchone())


def get_case_status(cur, case_id: str) -> Optional[str]:
    cur.execute(
        "SELECT status FROM clinical.worklist_case WHERE case_id = %s", (case_id,)
    )
    row = cur.fetchone()
    return None if row is None else str(row["status"])


def list_cases(
    cur, *, status: Optional[str] = None, assigned_to: Optional[str] = None,
    priority: Optional[str] = None, query: Optional[str] = None,
    unassigned: bool = False,
) -> List[Dict[str, Any]]:
    clauses: List[str] = []
    params: List[Any] = []
    if status is not None:
        clauses.append("status = %s")
        params.append(status)
    if assigned_to is not None:
        clauses.append("assigned_to = %s")
        params.append(assigned_to)
    if priority is not None:
        clauses.append("priority = %s")
        params.append(priority)
    if unassigned:
        clauses.append("(assigned_to IS NULL OR assigned_to = '')")
    if query:
        like = f"%{query.strip().lower()}%"
        clauses.append(
            "(lower(accession) LIKE %s OR lower(coalesce(specimen_id,'')) LIKE %s "
            "OR lower(coalesce(ordering_provider,'')) LIKE %s "
            "OR lower(coalesce(ordering_facility,'')) LIKE %s "
            "OR lower(coalesce(test_code,'')) LIKE %s)"
        )
        params.extend([like, like, like, like, like])
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    cur.execute(
        f"SELECT {_CASE_COLUMNS} FROM clinical.worklist_case{where} "
        "ORDER BY due_at NULLS LAST, created_at",
        tuple(params),
    )
    return [r for r in (_case_row(row) for row in cur.fetchall()) if r is not None]


def update_case(cur, case_id: str, patch: Dict[str, Any]) -> Dict[str, Any]:
    """Patch operational fields (assigned_to/priority/due_at/notes) on a case."""
    allowed = ("assigned_to", "priority", "due_at", "notes")
    sets: List[str] = []
    params: List[Any] = []
    for key in allowed:
        if key in patch:
            sets.append(f"{key} = %s")
            params.append(patch[key])
    sets.append("updated_at = now()")
    params.append(case_id)
    cur.execute(
        f"UPDATE clinical.worklist_case SET {', '.join(sets)} "
        f"WHERE case_id = %s RETURNING {_CASE_COLUMNS}",
        tuple(params),
    )
    row = _case_row(cur.fetchone())
    if row is None:
        raise LookupError(f"case {case_id} not visible to this tenant")
    return row


def transition_case(
    cur, case_id: str, *, current: str, to_status: str,
    actor: Optional[str] = None, note: Optional[str] = None,
) -> Dict[str, Any]:
    """Apply a (validated) status transition, stamping pipeline timestamps."""
    entry = {"from": current, "to": to_status, "at": _now_iso(),
             "actor": actor, "note": note}
    sets = ["status = %s", "updated_at = now()", "history = history || %s::jsonb"]
    params: List[Any] = [to_status, Jsonb([entry])]
    if to_status == "signed":
        sets.append("signed_at = coalesce(signed_at, now())")
    if to_status == "released":
        sets.append("released_at = now()")
    params.append(case_id)
    cur.execute(
        f"UPDATE clinical.worklist_case SET {', '.join(sets)} "
        f"WHERE case_id = %s RETURNING {_CASE_COLUMNS}",
        tuple(params),
    )
    row = _case_row(cur.fetchone())
    if row is None:
        raise LookupError(f"case {case_id} not visible to this tenant")
    return row


def attach_classification(cur, case_id: str, classification_id: str) -> Dict[str, Any]:
    existing = get_case(cur, case_id)
    if existing is None:
        raise LookupError(f"case {case_id} not visible to this tenant")
    ids = list(existing.get("classification_ids") or [])
    if classification_id not in ids:
        ids.append(classification_id)
        cur.execute(
            "UPDATE clinical.worklist_case SET classification_ids = %s::jsonb, "
            f"updated_at = now() WHERE case_id = %s RETURNING {_CASE_COLUMNS}",
            (Jsonb(ids), case_id),
        )
        row = _case_row(cur.fetchone())
        assert row is not None
        return row
    return existing
