"""Reviewer-workflow reporting endpoints.

These turn a persisted receipt + its history into the two human-review artifacts:
a technical reviewer report (audit *why* a tier was produced, before sign-off)
and a patient-safe summary. They are tenant-scoped reads that compose the store
with the :mod:`reporting` package; they make no clinical decision and release
nothing.
"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import PlainTextResponse

from reporting import (
    build_patient_summary,
    build_reviewer_report,
    render_patient_summary_markdown,
    render_reviewer_markdown,
)
from reporting.common import receipt_evidence, transcript_fields
from reporting.fhir import genomics_report_bundle

from ..auth import UserContext
from ..authz import require_permission
from ..deps import get_store, get_tenant_from_user
from ..store import ClinicalStore

router = APIRouter(tags=["reports"])


def _load_receipt(store: ClinicalStore, tenant_id: str, classification_id: str) -> Dict[str, Any]:
    receipt = store.get_classification(
        tenant_id=tenant_id, classification_id=classification_id
    )
    if receipt is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="classification not found")
    return receipt


@router.get("/classifications/{classification_id}/report/reviewer")
def reviewer_report(
    classification_id: str,
    format: str = Query(default="json", pattern="^(json|markdown)$"),
    tenant_id: str = Depends(get_tenant_from_user),
    user: UserContext = Depends(require_permission("classification:read")),
    store: ClinicalStore = Depends(get_store),
):
    receipt = _load_receipt(store, tenant_id, classification_id)
    variant_key = receipt.get("variant_key")
    report = build_reviewer_report(
        classification=receipt,
        # Surface the resolved evidence bundle persisted with the receipt so the
        # reviewer sees provider provenance, the MANE transcript identity, and PS4
        # cohort counts that were resolved at classification time.
        evidence_bundle=receipt_evidence(receipt),
        prior_classifications=store.list_classifications(
            tenant_id=tenant_id, variant_key=variant_key
        ),
        reanalysis_events=store.list_reanalysis_events(
            tenant_id=tenant_id, variant_key=variant_key
        ),
        alerts=store.list_alerts(tenant_id=tenant_id, variant_key=variant_key),
    )
    if format == "markdown":
        return PlainTextResponse(render_reviewer_markdown(report))
    return report


@router.get("/classifications/{classification_id}/report/summary")
def patient_summary(
    classification_id: str,
    format: str = Query(default="json", pattern="^(json|markdown)$"),
    tenant_id: str = Depends(get_tenant_from_user),
    user: UserContext = Depends(require_permission("classification:read")),
    store: ClinicalStore = Depends(get_store),
):
    receipt = _load_receipt(store, tenant_id, classification_id)
    report = build_patient_summary(classification=receipt)
    if format == "markdown":
        return PlainTextResponse(render_patient_summary_markdown(report))
    return report


@router.get("/classifications/{classification_id}/report/fhir")
def fhir_report(
    classification_id: str,
    tenant_id: str = Depends(get_tenant_from_user),
    user: UserContext = Depends(require_permission("classification:read")),
    store: ClinicalStore = Depends(get_store),
):
    receipt = _load_receipt(store, tenant_id, classification_id)
    variant_key = receipt.get("variant_key")
    if not variant_key:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="classification is missing variant_key; cannot render FHIR report",
        )
    issued = receipt.get("signed_off_at") if receipt.get("signed_off_by") else None
    # Carry the MANE Select transcript identity (job1 task 4) resolved at
    # classification time into the FHIR genomics bundle; all fields are None when
    # no transcript was resolved, so the serializer simply omits them.
    tx = transcript_fields(receipt_evidence(receipt))
    return genomics_report_bundle(
        receipt,
        variant_key=variant_key,
        gene=tx["gene"],
        transcript=tx["transcript"],
        hgvs_c=tx["hgvs_c"],
        hgvs_p=tx["hgvs_p"],
        issued=issued,
        effective=issued,
        signer=receipt.get("signed_off_by"),
        signed=not receipt.get("is_draft", True),
        report_id=classification_id,
    )
