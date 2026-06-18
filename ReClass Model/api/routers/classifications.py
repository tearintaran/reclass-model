"""Classification receipts: persist, read, list, and credentialed sign-off.

Every route here is tenant-scoped (``get_tenant_id`` -> the store opens a
``tenant_session``). A persisted receipt starts life as a **draft**
(``signed_off_by`` is NULL); only ``/sign-off`` — a credentialed human action —
releases it for clinical use. The API never auto-signs.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from engine.scoring import classify

from ..auth import UserContext
from ..authz import require_permission
from ..deps import get_audit_log, get_resolver, get_store, get_tenant_from_user
from ..evidence_resolver import EvidenceResolver
from ..schemas import PersistRequest, SignOffRequest
from ..service import classification_response, resolve_evidence
from ..store import ClinicalStore
from ..audit import AuditLog

router = APIRouter(tags=["classifications"])


def _locus_values(req_variant) -> tuple[str, int, str, str, str]:
    if not req_variant.has_locus:
        raise HTTPException(
            status_code=422,
            detail="persisting a clinical classification requires a full "
                   "(chrom,pos,ref,alt) locus",
        )
    return (
        str(req_variant.chrom),
        int(req_variant.pos),
        str(req_variant.ref),
        str(req_variant.alt),
        str(req_variant.build),
    )


@router.post("/classifications", status_code=status.HTTP_201_CREATED)
def create_classification(
    req: PersistRequest,
    tenant_id: str = Depends(get_tenant_from_user),
    user: UserContext = Depends(require_permission("classification:write")),
    store: ClinicalStore = Depends(get_store),
    resolver: EvidenceResolver = Depends(get_resolver),
    audit: AuditLog = Depends(get_audit_log),
) -> Dict[str, Any]:
    chrom, pos, ref, alt, build = _locus_values(req.variant)
    events, provenance = resolve_evidence(
        req.evidence, resolver, fallback_variant=req.variant
    )
    clf = classify(events)
    receipt = store.insert_classification(
        tenant_id=tenant_id,
        chrom=chrom, pos=pos, ref=ref, alt=alt, build=build,
        classification=clf, patient_mrn=req.patient_mrn,
        # Persist the resolved evidence bundle (transcript identity + PS4 cohort
        # counts + provenance) so reviewer/FHIR reports can surface it; None when
        # the result was scored from direct events/signals (no resolved bundle).
        evidence=provenance.get("bundle"),
    )
    audit.append(
        tenant_id=tenant_id,
        actor_id=user.user_id,
        action="classification.create",
        resource_type="classification",
        resource_id=str(receipt["classification_id"]),
        detail={"tier": clf.tier, "variant_key": req.variant.variant_key()},
    )
    return classification_response(clf, provenance, receipt=receipt)


@router.get("/classifications")
def list_classifications(
    variant_key: Optional[str] = Query(default=None),
    tenant_id: str = Depends(get_tenant_from_user),
    user: UserContext = Depends(require_permission("classification:read")),
    store: ClinicalStore = Depends(get_store),
) -> List[Dict[str, Any]]:
    return store.list_classifications(tenant_id=tenant_id, variant_key=variant_key)


@router.get("/classifications/{classification_id}")
def get_classification(
    classification_id: str,
    tenant_id: str = Depends(get_tenant_from_user),
    user: UserContext = Depends(require_permission("classification:read")),
    store: ClinicalStore = Depends(get_store),
) -> Dict[str, Any]:
    receipt = store.get_classification(
        tenant_id=tenant_id, classification_id=classification_id
    )
    if receipt is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="classification not found")
    return receipt


@router.post("/classifications/{classification_id}/sign-off")
def sign_off_classification(
    classification_id: str,
    req: SignOffRequest,
    tenant_id: str = Depends(get_tenant_from_user),
    user: UserContext = Depends(require_permission("classification:sign_off")),
    store: ClinicalStore = Depends(get_store),
    audit: AuditLog = Depends(get_audit_log),
) -> Dict[str, Any]:
    try:
        receipt = store.sign_off(
            tenant_id=tenant_id,
            classification_id=classification_id,
            signed_off_by=req.signed_off_by,
        )
    except LookupError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="classification not found")
    audit.append(
        tenant_id=tenant_id,
        actor_id=user.user_id,
        action="classification.sign_off",
        resource_type="classification",
        resource_id=classification_id,
        detail={
            "signed_off_by": req.signed_off_by,
            "credential": req.credential,
            "tier": receipt.get("tier"),
        },
    )
    return receipt
