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

from ..deps import get_resolver, get_store, get_tenant_id
from ..evidence_resolver import EvidenceResolver
from ..schemas import ReanalysisRequest
from ..service import resolve_evidence
from ..store import ClinicalStore

router = APIRouter(tags=["reanalysis"])


@router.post("/reanalysis/run")
def run_reanalysis(
    req: ReanalysisRequest,
    tenant_id: str = Depends(get_tenant_id),
    store: ClinicalStore = Depends(get_store),
    resolver: EvidenceResolver = Depends(get_resolver),
) -> Dict[str, Any]:
    if not req.variant.has_locus:
        raise HTTPException(
            status_code=422,
            detail="reanalysis requires a full (chrom,pos,ref,alt) locus",
        )
    events, provenance = resolve_evidence(
        req.evidence, resolver, fallback_variant=req.variant
    )
    result = store.run_reanalysis(
        tenant_id=tenant_id,
        chrom=str(req.variant.chrom), pos=int(req.variant.pos),  # type: ignore[arg-type]
        ref=req.variant.ref, alt=req.variant.alt, build=req.variant.build,
        new_events=events, engine_version=C.ENGINE_VERSION,
        trigger=req.trigger, patient_mrn=req.patient_mrn,
    )
    return {
        "result": result,
        "warnings": provenance.get("warnings", []),
        "provider_versions": provenance.get("provider_versions", {}),
        "evidence": provenance.get("bundle"),
    }
