"""Evidence resolution **and** the evidence workbench / operations surface (job1).

Two layers share this router:

  * ``/evidence/resolve`` + ``/evidence/providers`` — the original de-identified
    resolution step (works on a public locus / Variation ID; no tenant data).

  * the **evidence workbench** (job1): persist reviewer/pipeline-entered structured
    evidence with full provenance, report evidence-coverage breakdowns, surface and
    enqueue curation work items, and dry-run VCF/CSV/batch imports. Coverage and
    curation are tenant-scoped operational surfaces; reviewer-entered evidence is
    de-identified (keyed only on the public ``variant_key``), like the rest of the
    research domain.

The workbench store is resolved from ``app.state.workbench_store`` and falls back to
an in-memory store (mirroring the audit-log dependency), so these endpoints serve in
CI without a database. Permissions reuse the existing role model: reads use
``classification:read`` / ``evidence:resolve``; writes use ``classification:write``.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from evidence import coverage as coverage_mod
from evidence import curation as curation_mod
from evidence.workbench import (
    CURATION_STATES,
    InMemoryWorkbenchStore,
    ReviewerEvidence,
    WorkbenchError,
    WorkbenchStore,
)
from ingest.batch_import import BatchImportError, import_batch
from ingest.csv_import import import_csv
from ingest.vcf_import import import_vcf

from ..auth import UserContext
from ..authz import require_permission
from ..deps import get_resolver, get_tenant_from_user
from ..evidence_resolver import EvidenceResolver
from ..schemas import (
    EvidenceBundleResponse,
    ProviderInfo,
    ProvidersResponse,
    ResolveRequest,
    VariantRef,
)

router = APIRouter(tags=["evidence"])


def get_workbench_store(request: Request) -> WorkbenchStore:
    """Resolve the workbench store, falling back to an in-memory one (like audit)."""
    store = getattr(request.app.state, "workbench_store", None)
    if store is None:
        store = InMemoryWorkbenchStore()
        request.app.state.workbench_store = store
    return store


# --------------------------------------------------------------------------- #
# Resolution (original surface)                                               #
# --------------------------------------------------------------------------- #
@router.get("/evidence/providers", response_model=ProvidersResponse)
def list_providers(
    user: UserContext = Depends(require_permission("evidence:resolve")),
    resolver: EvidenceResolver = Depends(get_resolver),
) -> ProvidersResponse:
    """List the *configured* evidence providers and their source versions.

    De-identified (no patient data, no variant), like resolve. The reviewer UI
    uses this to populate its provider panel without first running a resolve, so
    the available provider set is never hardcoded on the client.
    """
    return ProvidersResponse(
        providers=[ProviderInfo(**p) for p in resolver.provider_catalog],
    )


@router.post("/evidence/resolve", response_model=EvidenceBundleResponse)
def resolve_evidence(
    req: ResolveRequest,
    user: UserContext = Depends(require_permission("evidence:resolve")),
    resolver: EvidenceResolver = Depends(get_resolver),
) -> EvidenceBundleResponse:
    result = resolver.resolve(
        req.variant.to_provider_input(),
        variant_key=req.variant.variant_key(),
        providers=req.providers,
    )
    bundle = result["bundle"].to_dict()
    per_provider = {k: v.to_dict() for k, v in result["per_provider"].items()}
    return EvidenceBundleResponse(
        variant_key=bundle["variant_key"],
        events=bundle["events"],
        provider_versions=bundle["provider_versions"],
        source_records=bundle["source_records"],
        warnings=bundle["warnings"],
        match=bundle["match"],
        transcript=bundle["transcript"],
        cohort_counts=bundle["cohort_counts"],
        per_provider=per_provider,
    )


# --------------------------------------------------------------------------- #
# Workbench request models                                                    #
# --------------------------------------------------------------------------- #
class ReviewerEvidenceRequest(BaseModel):
    """Reviewer/pipeline-entered structured evidence for one variant (job1 task 1)."""

    variant: VariantRef
    acmg_criterion: str
    evidence_direction: str = Field(pattern="^(pathogenic|benign|neutral)$")
    applied_strength: Optional[str] = None
    points: Optional[float] = None
    source: str = "reviewer"
    source_version: Optional[str] = None
    source_url: Optional[str] = None
    access_date: Optional[str] = None
    reviewer: Optional[str] = None
    reviewer_credential: Optional[str] = None
    status: str = "active"
    notes: Optional[str] = None
    expires_at: Optional[str] = None
    re_review_at: Optional[str] = None
    record: Dict[str, Any] = Field(default_factory=dict)


class EvidenceStatusRequest(BaseModel):
    status: str


class CoverageRequest(BaseModel):
    """Record/refresh an evidence-coverage observation for one (tenant, variant)."""

    variant: VariantRef
    present_criteria: List[str] = Field(default_factory=list)
    gene: Optional[str] = None
    vcep: Optional[str] = None
    disease: Optional[str] = None
    variant_class: Optional[str] = None
    provider: Optional[str] = None


class CurationScanRequest(BaseModel):
    """Resolve a variant, surface curation items, and optionally enqueue them."""

    variant: VariantRef
    providers: Optional[List[str]] = None
    enqueue: bool = False


class CurationStateRequest(BaseModel):
    state: str


class ImportPreviewRequest(BaseModel):
    """Dry-run VCF/CSV variant import: normalize, dedup, optional resolve preview."""

    format: str = Field(pattern="^(vcf|csv)$")
    content: str
    providers: Optional[List[str]] = None
    resolve: bool = False
    delimiter: Optional[str] = None


class BatchEvidenceImportRequest(BaseModel):
    """Batch import of validated upstream evidence (functional/phenotype/family/...)."""

    source_kind: str
    records: List[Dict[str, Any]] = Field(default_factory=list)
    access_date: Optional[str] = None


def _require_variant_key(variant: VariantRef) -> str:
    key = variant.variant_key()
    if key is None:
        raise HTTPException(
            status_code=422,
            detail="a full (chrom,pos,ref,alt) locus is required for workbench evidence",
        )
    return key


# --------------------------------------------------------------------------- #
# Workbench: reviewer-entered evidence (job1 task 1)                          #
# --------------------------------------------------------------------------- #
@router.get("/evidence/workbench/criteria")
def workbench_criteria(
    user: UserContext = Depends(require_permission("evidence:resolve")),
) -> Dict[str, str]:
    """The ACMG criteria the workbench is built to capture (drives the UI panel)."""
    from evidence.workbench import WORKBENCH_CRITERIA

    return dict(WORKBENCH_CRITERIA)


@router.post("/evidence/workbench/evidence", status_code=201)
def submit_reviewer_evidence(
    req: ReviewerEvidenceRequest,
    user: UserContext = Depends(require_permission("classification:write")),
    store: WorkbenchStore = Depends(get_workbench_store),
) -> Dict[str, Any]:
    """Persist one reviewer-entered structured evidence record with full provenance."""
    variant_key = _require_variant_key(req.variant)
    try:
        evidence = ReviewerEvidence(
            variant_key=variant_key,
            acmg_criterion=req.acmg_criterion,
            evidence_direction=req.evidence_direction,
            reviewer=req.reviewer or user.display_name or user.user_id,
            applied_strength=req.applied_strength,
            points=req.points,
            source=req.source,
            source_version=req.source_version,
            source_url=req.source_url,
            access_date=req.access_date,
            reviewer_credential=req.reviewer_credential,
            status=req.status,
            notes=req.notes,
            expires_at=req.expires_at,
            re_review_at=req.re_review_at,
            record=dict(req.record),
        )
        return store.add_evidence(evidence)
    except WorkbenchError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/evidence/workbench/evidence")
def list_reviewer_evidence(
    variant_key: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    user: UserContext = Depends(require_permission("classification:read")),
    store: WorkbenchStore = Depends(get_workbench_store),
) -> List[Dict[str, Any]]:
    """List reviewer-entered evidence, optionally filtered by variant key / status."""
    return store.list_evidence(variant_key=variant_key, status=status)


@router.post("/evidence/workbench/evidence/{reviewer_evidence_id}/status")
def update_reviewer_evidence_status(
    reviewer_evidence_id: str,
    req: EvidenceStatusRequest,
    user: UserContext = Depends(require_permission("classification:write")),
    store: WorkbenchStore = Depends(get_workbench_store),
) -> Dict[str, Any]:
    """Transition a reviewer evidence record's status (e.g. withdraw / supersede)."""
    try:
        return store.set_status(reviewer_evidence_id, req.status)
    except WorkbenchError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/evidence/workbench/expire")
def expire_reviewer_evidence(
    as_of: Optional[str] = Query(default=None),
    user: UserContext = Depends(require_permission("classification:write")),
    store: WorkbenchStore = Depends(get_workbench_store),
) -> Dict[str, Any]:
    """Expire active reviewer evidence past its re-review deadline (re-review gate)."""
    expired = store.expire_due(as_of=as_of)
    return {"expired": expired, "count": len(expired)}


# --------------------------------------------------------------------------- #
# Workbench: evidence coverage (job1 task 2)                                  #
# --------------------------------------------------------------------------- #
@router.post("/evidence/coverage", status_code=201)
def record_coverage(
    req: CoverageRequest,
    tenant_id: str = Depends(get_tenant_from_user),
    user: UserContext = Depends(require_permission("classification:write")),
    store: WorkbenchStore = Depends(get_workbench_store),
) -> Dict[str, Any]:
    """Record/refresh a coverage observation; the missing/blocked status is derived."""
    variant_key = _require_variant_key(req.variant)
    record = coverage_mod.compute_coverage(
        variant_key, req.present_criteria,
        variant_class=req.variant_class, gene=req.gene, vcep=req.vcep,
        disease=req.disease, provider=req.provider,
    )
    return store.upsert_coverage(tenant_id=tenant_id, record=record)


@router.get("/evidence/coverage")
def coverage_summary(
    by: Optional[str] = Query(default=None),
    tenant_id: str = Depends(get_tenant_from_user),
    user: UserContext = Depends(require_permission("classification:read")),
    store: WorkbenchStore = Depends(get_workbench_store),
) -> Dict[str, Any]:
    """Blocked-case coverage breakdown. ``by`` returns a single dimension's roll-up."""
    rows = store.list_coverage(tenant_id=tenant_id)
    if by is not None:
        if by not in coverage_mod.DIMENSIONS:
            raise HTTPException(
                status_code=422,
                detail=f"coverage dimension must be one of {coverage_mod.DIMENSIONS}",
            )
        return {"by": by, "buckets": coverage_mod.rollup(rows, by)}
    return coverage_mod.summarize(rows)


# --------------------------------------------------------------------------- #
# Workbench: curation queue (job1 task 3)                                     #
# --------------------------------------------------------------------------- #
@router.post("/evidence/curation/scan")
def scan_curation(
    req: CurationScanRequest,
    tenant_id: str = Depends(get_tenant_from_user),
    user: UserContext = Depends(require_permission("classification:write")),
    resolver: EvidenceResolver = Depends(get_resolver),
    store: WorkbenchStore = Depends(get_workbench_store),
) -> Dict[str, Any]:
    """Resolve a variant, surface curation gaps, and (optionally) enqueue them."""
    result = resolver.resolve(
        req.variant.to_provider_input(),
        variant_key=req.variant.variant_key(),
        providers=req.providers,
    )
    items = curation_mod.scan_bundle(result["bundle"])
    enqueued: List[Dict[str, Any]] = []
    if req.enqueue:
        for item in items:
            row = store.enqueue_curation(tenant_id=tenant_id, item=item)
            if row is not None:
                enqueued.append(row)
    return {
        "items": [i.to_dict() for i in items],
        "enqueued": enqueued,
        "enqueued_count": len(enqueued),
    }


@router.get("/evidence/curation")
def list_curation(
    kind: Optional[str] = Query(default=None),
    state: Optional[str] = Query(default=None),
    tenant_id: str = Depends(get_tenant_from_user),
    user: UserContext = Depends(require_permission("classification:read")),
    store: WorkbenchStore = Depends(get_workbench_store),
) -> List[Dict[str, Any]]:
    """List tenant curation work items, optionally filtered by kind / state."""
    if kind is not None and kind not in curation_mod.CURATION_KINDS:
        raise HTTPException(
            status_code=422,
            detail=f"curation kind must be one of {curation_mod.CURATION_KINDS}",
        )
    return store.list_curation(tenant_id=tenant_id, kind=kind, state=state)


@router.post("/evidence/curation/{curation_id}/state")
def update_curation_state(
    curation_id: str,
    req: CurationStateRequest,
    tenant_id: str = Depends(get_tenant_from_user),
    user: UserContext = Depends(require_permission("classification:write")),
    store: WorkbenchStore = Depends(get_workbench_store),
) -> Dict[str, Any]:
    """Move a curation item through its lifecycle (in_review / resolved / dismissed)."""
    if req.state not in CURATION_STATES:
        raise HTTPException(
            status_code=422,
            detail=f"curation state must be one of {CURATION_STATES}",
        )
    try:
        return store.set_curation_state(
            tenant_id=tenant_id, curation_id=curation_id, state=req.state
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# --------------------------------------------------------------------------- #
# Workbench: VCF/CSV + batch import (job1 task 4-5)                           #
# --------------------------------------------------------------------------- #
@router.post("/evidence/import/preview")
def import_preview(
    req: ImportPreviewRequest,
    user: UserContext = Depends(require_permission("evidence:resolve")),
    resolver: EvidenceResolver = Depends(get_resolver),
) -> Dict[str, Any]:
    """Dry-run VCF/CSV import: identity normalization, dedup, evidence-resolve preview."""
    preview_resolver = resolver if req.resolve else None
    if req.format == "vcf":
        return import_vcf(req.content, resolver=preview_resolver, providers=req.providers)
    return import_csv(
        req.content, delimiter=req.delimiter,
        resolver=preview_resolver, providers=req.providers,
    )


@router.post("/evidence/import/batch")
def import_batch_evidence(
    req: BatchEvidenceImportRequest,
    user: UserContext = Depends(require_permission("classification:write")),
) -> Dict[str, Any]:
    """Batch-import upstream evidence; PHI is scrubbed before any research mapping."""
    try:
        result = import_batch(req.source_kind, req.records, access_date=req.access_date)
    except BatchImportError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return result["report"]
