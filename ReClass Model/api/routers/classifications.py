"""Classification receipts: persist, read, list, and credentialed sign-off.

Every route here is tenant-scoped (``get_tenant_id`` -> the store opens a
``tenant_session``). A persisted receipt starts life as a **draft**
(``signed_off_by`` is NULL); only ``/sign-off`` — a credentialed human action —
releases it for clinical use. The API never auto-signs.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from engine import config as C
from engine.scoring import classify

from ..deps import get_resolver, get_store, get_tenant_id
from ..evidence_resolver import EvidenceResolver
from ..schemas import PersistRequest, SignOffRequest
from ..service import classification_response, resolve_evidence
from ..store import ClinicalStore

router = APIRouter(tags=["classifications"])


def _require_locus(req_variant) -> None:
    if not req_variant.has_locus:
        raise HTTPException(
            status_code=422,
            detail="persisting a clinical classification requires a full "
                   "(chrom,pos,ref,alt) locus",
        )


@router.post("/classifications", status_code=status.HTTP_201_CREATED)
def create_classification(
    req: PersistRequest,
    tenant_id: str = Depends(get_tenant_id),
    store: ClinicalStore = Depends(get_store),
    resolver: EvidenceResolver = Depends(get_resolver),
) -> Dict[str, Any]:
    _require_locus(req.variant)
    events, provenance = resolve_evidence(
        req.evidence, resolver, fallback_variant=req.variant
    )
    clf = classify(events)
    receipt = store.insert_classification(
        tenant_id=tenant_id,
        chrom=str(req.variant.chrom), pos=int(req.variant.pos),  # type: ignore[arg-type]
        ref=req.variant.ref, alt=req.variant.alt, build=req.variant.build,
        classification=clf, patient_mrn=req.patient_mrn,
    )
    return classification_response(clf, provenance, receipt=receipt)


@router.get("/classifications")
def list_classifications(
    variant_key: Optional[str] = Query(default=None),
    tenant_id: str = Depends(get_tenant_id),
    store: ClinicalStore = Depends(get_store),
) -> List[Dict[str, Any]]:
    return store.list_classifications(tenant_id=tenant_id, variant_key=variant_key)


@router.get("/classifications/{classification_id}")
def get_classification(
    classification_id: str,
    tenant_id: str = Depends(get_tenant_id),
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
    tenant_id: str = Depends(get_tenant_id),
    store: ClinicalStore = Depends(get_store),
) -> Dict[str, Any]:
    try:
        return store.sign_off(
            tenant_id=tenant_id,
            classification_id=classification_id,
            signed_off_by=req.signed_off_by,
        )
    except LookupError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="classification not found")
