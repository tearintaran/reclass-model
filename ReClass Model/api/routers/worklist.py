"""Variant case worklist — the primary daily reviewer surface.

Every route is tenant-scoped (``get_tenant_from_user`` -> the store partitions /
RLS-isolates by tenant) and audited. The worklist layers a case/order model above
the de-identified classification receipts: a case has an accession, an assignee, a
turnaround clock, and a status that moves through ``draft -> in_review -> signed ->
released`` (state-machine validated in :mod:`worklist.case`).

PHI boundary: list views and the default detail view are **de-identified** (PHI
redacted). The full record — patient MRN/name and clinical indication — is returned
only when the caller asks for it *and* holds ``case:read_phi``; that access is
audited as a distinct event.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from worklist.case import (
    Case,
    CaseError,
    InMemoryWorklistStore,
    PRIORITIES,
    STATUSES,
    WorklistStore,
)

from ..audit import AuditLog
from ..auth import UserContext
from ..authz import require_permission, user_can
from ..deps import get_audit_log, get_store, get_tenant_from_user
from ..schemas import (
    CaseAttachRequest,
    CaseBulkAssignRequest,
    CaseBulkTransitionRequest,
    CaseCreateRequest,
    CaseTransitionRequest,
    CaseUpdateRequest,
)
from ..store import ClinicalStore

router = APIRouter(tags=["worklist"])


def get_worklist_store(request: Request) -> WorklistStore:
    """Resolve the worklist store, falling back to an in-memory one (like workbench)."""
    store = getattr(request.app.state, "worklist_store", None)
    if store is None:
        store = InMemoryWorklistStore()
        request.app.state.worklist_store = store
    return store


def _actor(user: UserContext) -> str:
    return user.display_name or user.user_id


def _dedupe(case_ids: List[str]) -> List[str]:
    """De-duplicate the requested ids while preserving order, so a doubly-selected
    case is applied (and reported) exactly once."""
    seen: set = set()
    out: List[str] = []
    for cid in case_ids:
        cid = str(cid)
        if cid not in seen:
            seen.add(cid)
            out.append(cid)
    return out


@router.post("/worklist/cases", status_code=status.HTTP_201_CREATED)
def create_case(
    req: CaseCreateRequest,
    tenant_id: str = Depends(get_tenant_from_user),
    user: UserContext = Depends(require_permission("case:write")),
    store: WorklistStore = Depends(get_worklist_store),
    audit: AuditLog = Depends(get_audit_log),
) -> Dict[str, Any]:
    try:
        case = Case(
            tenant_id=tenant_id,
            accession=req.accession,
            priority=req.priority,
            assigned_to=req.assigned_to,
            specimen_id=req.specimen_id,
            specimen_type=req.specimen_type,
            ordering_provider=req.ordering_provider,
            ordering_facility=req.ordering_facility,
            test_code=req.test_code,
            patient_mrn=req.patient_mrn,
            patient_name=req.patient_name,
            indication=req.indication,
            received_at=req.received_at,
            due_at=req.due_at,
            notes=req.notes,
            classification_ids=list(req.classification_ids),
        )
        created = store.create_case(case)
    except CaseError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    audit.append(
        tenant_id=tenant_id,
        actor_id=user.user_id,
        action="case.create",
        resource_type="case",
        resource_id=str(created["case_id"]),
        detail={"accession": req.accession, "priority": created["priority"],
                "has_phi": any(getattr(req, f) for f in ("patient_mrn", "patient_name", "indication"))},
    )
    return created


@router.get("/worklist/cases")
def list_cases(
    status_filter: Optional[str] = Query(default=None, alias="status"),
    assigned_to: Optional[str] = Query(default=None),
    priority: Optional[str] = Query(default=None),
    sla_status: Optional[str] = Query(default=None),
    unassigned: bool = Query(default=False),
    q: Optional[str] = Query(default=None),
    tenant_id: str = Depends(get_tenant_from_user),
    user: UserContext = Depends(require_permission("case:read")),
    store: WorklistStore = Depends(get_worklist_store),
) -> List[Dict[str, Any]]:
    if priority is not None and priority not in PRIORITIES:
        raise HTTPException(status_code=422,
                            detail=f"priority must be one of {PRIORITIES}")
    return store.list_cases(
        tenant_id=tenant_id, status=status_filter, assigned_to=assigned_to,
        priority=priority, sla_status=sla_status, unassigned=unassigned, query=q,
    )


@router.get("/worklist/metrics")
def worklist_metrics(
    tenant_id: str = Depends(get_tenant_from_user),
    user: UserContext = Depends(require_permission("case:read")),
    store: WorklistStore = Depends(get_worklist_store),
) -> Dict[str, Any]:
    """Queue summary for the worklist header / operational dashboard."""
    return store.metrics(tenant_id=tenant_id)


# NOTE: the two bulk routes are declared *before* the parameterised
# ``/worklist/cases/{case_id}`` routes so the literal ``bulk`` segment is matched
# first (Starlette resolves routes in declaration order). Case ids are UUIDs, so a
# real case is never named "bulk", but ordering keeps the match unambiguous.
@router.post("/worklist/cases/bulk/assign")
def bulk_assign_cases(
    req: CaseBulkAssignRequest,
    tenant_id: str = Depends(get_tenant_from_user),
    user: UserContext = Depends(require_permission("case:write")),
    store: WorklistStore = Depends(get_worklist_store),
    audit: AuditLog = Depends(get_audit_log),
) -> Dict[str, Any]:
    """Assign or unassign many cases in one call. Per-case partial success: the
    response carries a ``summary`` and an ordered ``results`` list."""
    case_ids = _dedupe(req.case_ids)
    result = store.bulk_assign(
        tenant_id=tenant_id, case_ids=case_ids,
        assigned_to=req.assigned_to, actor=_actor(user),
    )
    audit.append(
        tenant_id=tenant_id,
        actor_id=user.user_id,
        action="case.bulk_assign",
        resource_type="case",
        resource_id="bulk",
        detail={
            "assigned_to": req.assigned_to,
            "requested": result["summary"]["requested"],
            "succeeded": [r["case_id"] for r in result["results"] if r["ok"]],
            "failed": [r["case_id"] for r in result["results"] if not r["ok"]],
        },
    )
    return result


@router.post("/worklist/cases/bulk/transition")
def bulk_transition_cases(
    req: CaseBulkTransitionRequest,
    tenant_id: str = Depends(get_tenant_from_user),
    user: UserContext = Depends(require_permission("case:transition")),
    store: WorklistStore = Depends(get_worklist_store),
    audit: AuditLog = Depends(get_audit_log),
) -> Dict[str, Any]:
    """Move many cases to one target status. Each case is validated independently
    against its own current status, so a mixed-status selection transitions the
    legal cases and reports the rest in ``results``."""
    if req.to_status not in STATUSES:
        raise HTTPException(status_code=422,
                            detail=f"status must be one of {STATUSES}")
    case_ids = _dedupe(req.case_ids)
    result = store.bulk_transition(
        tenant_id=tenant_id, case_ids=case_ids, to_status=req.to_status,
        actor=_actor(user), note=req.note,
    )
    audit.append(
        tenant_id=tenant_id,
        actor_id=user.user_id,
        action="case.bulk_transition",
        resource_type="case",
        resource_id="bulk",
        detail={
            "to_status": req.to_status,
            "note": req.note,
            "requested": result["summary"]["requested"],
            "succeeded": [r["case_id"] for r in result["results"] if r["ok"]],
            "failed": [r["case_id"] for r in result["results"] if not r["ok"]],
        },
    )
    return result


@router.get("/worklist/cases/{case_id}")
def get_case(
    case_id: str,
    include_phi: bool = Query(default=False),
    tenant_id: str = Depends(get_tenant_from_user),
    user: UserContext = Depends(require_permission("case:read")),
    store: WorklistStore = Depends(get_worklist_store),
    audit: AuditLog = Depends(get_audit_log),
) -> Dict[str, Any]:
    if include_phi and not user_can(user, "case:read_phi"):
        raise HTTPException(status_code=403, detail="permission denied: case:read_phi")
    case = store.get_case(tenant_id=tenant_id, case_id=case_id, include_phi=include_phi)
    if case is None:
        raise HTTPException(status_code=404, detail="case not found")
    if include_phi:
        audit.append(
            tenant_id=tenant_id,
            actor_id=user.user_id,
            action="case.read_phi",
            resource_type="case",
            resource_id=case_id,
            detail={"accession": case.get("accession")},
        )
    return case


@router.patch("/worklist/cases/{case_id}")
def update_case(
    case_id: str,
    req: CaseUpdateRequest,
    tenant_id: str = Depends(get_tenant_from_user),
    user: UserContext = Depends(require_permission("case:write")),
    store: WorklistStore = Depends(get_worklist_store),
    audit: AuditLog = Depends(get_audit_log),
) -> Dict[str, Any]:
    # Only patch the fields the client actually sent (model_fields_set), so an
    # omitted field is left unchanged while an explicit null clears it.
    sent = req.model_fields_set
    patch: Dict[str, Any] = {k: getattr(req, k)
                             for k in ("assigned_to", "priority", "due_at", "notes")
                             if k in sent}
    if not patch:
        raise HTTPException(status_code=422, detail="no updatable fields supplied")
    try:
        updated = store.update_case(
            tenant_id=tenant_id, case_id=case_id, actor=_actor(user), **patch
        )
    except CaseError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except LookupError:
        raise HTTPException(status_code=404, detail="case not found")
    audit.append(
        tenant_id=tenant_id,
        actor_id=user.user_id,
        action="case.update",
        resource_type="case",
        resource_id=case_id,
        detail={"fields": sorted(patch.keys())},
    )
    return updated


@router.post("/worklist/cases/{case_id}/transition")
def transition_case(
    case_id: str,
    req: CaseTransitionRequest,
    tenant_id: str = Depends(get_tenant_from_user),
    user: UserContext = Depends(require_permission("case:transition")),
    store: WorklistStore = Depends(get_worklist_store),
    audit: AuditLog = Depends(get_audit_log),
) -> Dict[str, Any]:
    try:
        updated = store.transition_case(
            tenant_id=tenant_id, case_id=case_id, to_status=req.to_status,
            actor=_actor(user), note=req.note,
        )
    except CaseError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except LookupError:
        raise HTTPException(status_code=404, detail="case not found")
    audit.append(
        tenant_id=tenant_id,
        actor_id=user.user_id,
        action="case.transition",
        resource_type="case",
        resource_id=case_id,
        detail={"to_status": req.to_status, "note": req.note},
    )
    return updated


@router.post("/worklist/cases/{case_id}/classifications")
def attach_classification(
    case_id: str,
    req: CaseAttachRequest,
    tenant_id: str = Depends(get_tenant_from_user),
    user: UserContext = Depends(require_permission("case:write")),
    store: WorklistStore = Depends(get_worklist_store),
    clinical: ClinicalStore = Depends(get_store),
    audit: AuditLog = Depends(get_audit_log),
) -> Dict[str, Any]:
    # The classification must exist for this tenant before it can be linked.
    receipt = clinical.get_classification(
        tenant_id=tenant_id, classification_id=req.classification_id
    )
    if receipt is None:
        raise HTTPException(status_code=404, detail="classification not found")
    try:
        updated = store.attach_classification(
            tenant_id=tenant_id, case_id=case_id,
            classification_id=req.classification_id, actor=_actor(user),
        )
    except LookupError:
        raise HTTPException(status_code=404, detail="case not found")
    audit.append(
        tenant_id=tenant_id,
        actor_id=user.user_id,
        action="case.attach_classification",
        resource_type="case",
        resource_id=case_id,
        detail={"classification_id": req.classification_id},
    )
    return updated
