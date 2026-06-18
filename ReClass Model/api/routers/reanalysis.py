"""``POST /reanalysis/run`` — recompute a variant from current evidence.

A thin wrapper over ``monitoring.reanalysis.reanalyze`` (via the store): it
persists a new receipt only when the result actually changes, writes a
``clinical.alert`` only on a tier crossing, and records a same-tier point change
as an audit event that pages no one. The endpoint adds no logic of its own.
"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException

from engine import config as C
from engine.scoring import classify  # noqa: F401 (documents the engine dependency)

from ..auth import UserContext
from ..authz import require_permission
from ..deps import get_audit_log, get_resolver, get_store, get_tenant_from_user
from ..evidence_resolver import EvidenceResolver
from ..schemas import ReanalysisRequest
from ..service import resolve_evidence
from ..store import ClinicalStore
from ..audit import AuditLog

router = APIRouter(tags=["reanalysis"])


def _locus_values(req_variant) -> tuple[str, int, str, str, str]:
    if not req_variant.has_locus:
        raise HTTPException(
            status_code=422,
            detail="reanalysis requires a full (chrom,pos,ref,alt) locus",
        )
    return (
        str(req_variant.chrom),
        int(req_variant.pos),
        str(req_variant.ref),
        str(req_variant.alt),
        str(req_variant.build),
    )


@router.post("/reanalysis/run")
def run_reanalysis(
    req: ReanalysisRequest,
    tenant_id: str = Depends(get_tenant_from_user),
    user: UserContext = Depends(require_permission("reanalysis:run")),
    store: ClinicalStore = Depends(get_store),
    resolver: EvidenceResolver = Depends(get_resolver),
    audit: AuditLog = Depends(get_audit_log),
) -> Dict[str, Any]:
    chrom, pos, ref, alt, build = _locus_values(req.variant)
    events, provenance = resolve_evidence(
        req.evidence, resolver, fallback_variant=req.variant
    )
    result = store.run_reanalysis(
        tenant_id=tenant_id,
        chrom=chrom, pos=pos, ref=ref, alt=alt, build=build,
        new_events=events, engine_version=C.ENGINE_VERSION,
        trigger=req.trigger, patient_mrn=req.patient_mrn,
    )
    audit.append(
        tenant_id=tenant_id,
        actor_id=user.user_id,
        action="reanalysis.run",
        resource_type="variant",
        resource_id=req.variant.variant_key() or "",
        detail={
            "trigger": req.trigger,
            "changed": result.get("changed"),
            "crossed": result.get("crossed"),
            "alert_id": result.get("alert_id"),
        },
    )
    return {
        "result": result,
        "warnings": provenance.get("warnings", []),
        "provider_versions": provenance.get("provider_versions", {}),
        "evidence": provenance.get("bundle"),
    }
