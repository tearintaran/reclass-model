"""``POST /reanalysis/run`` — recompute a variant from current evidence.

A thin wrapper over ``monitoring.reanalysis.reanalyze`` (via the store): it
persists a new receipt only when the result actually changes, writes a
``clinical.alert`` only on a tier crossing, and records a same-tier point change
as an audit event that pages no one. It also emits webhook lifecycle events via
Job 3's outbound delivery seam.
"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Request

from engine import config as C
from engine.scoring import classify  # noqa: F401 (documents the engine dependency)
from monitoring.reanalysis import (
    operator_queue_view,
    operator_run_manifests,
    provider_cache_readiness,
    same_tier_changes,
)

from ..auth import UserContext
from ..authz import require_permission
from ..deps import get_audit_log, get_resolver, get_store, get_tenant_from_user
from ..evidence_resolver import EvidenceResolver
from ..schemas import ReanalysisPolicyRequest, ReanalysisRequest
from ..service import resolve_evidence
from ..store import ClinicalStore
from ..audit import AuditLog
from ..webhooks import emit_event

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


def _emit_reanalysis_events(
    request: Request,
    *,
    tenant_id: str,
    req: ReanalysisRequest,
    result: Dict[str, Any],
    provider_versions: Dict[str, Any],
) -> None:
    webhook_store = getattr(request.app.state, "webhook_store", None)
    if webhook_store is None:
        return
    variant_key = req.variant.variant_key() or ""
    payload = {
        "variant_key": variant_key,
        "trigger": req.trigger,
        "changed": result.get("changed"),
        "crossed": result.get("crossed"),
        "old_tier": result.get("old_tier"),
        "new_tier": result.get("new_tier"),
        "old_points": result.get("old_points"),
        "new_points": result.get("new_points"),
        "new_classification_id": result.get("new_classification_id"),
        "reanalysis_id": result.get("reanalysis_id"),
        "alert_id": result.get("alert_id"),
        "provider_versions": provider_versions,
    }
    source_id = (
        result.get("reanalysis_id")
        or result.get("new_classification_id")
        or variant_key
        or None
    )
    emit_event(
        webhook_store,
        tenant_id=tenant_id,
        event_type="reanalysis_completed",
        payload=payload,
        source_id=source_id,
    )
    if result.get("crossed"):
        emit_event(
            webhook_store,
            tenant_id=tenant_id,
            event_type="tier_crossing",
            payload=payload,
            source_id=result.get("alert_id") or source_id,
        )


@router.post("/reanalysis/run")
def run_reanalysis(
    req: ReanalysisRequest,
    request: Request,
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
    provider_versions = provenance.get("provider_versions", {})
    _emit_reanalysis_events(
        request,
        tenant_id=tenant_id,
        req=req,
        result=result,
        provider_versions=provider_versions,
    )
    return {
        "result": result,
        "warnings": provenance.get("warnings", []),
        "provider_versions": provider_versions,
        "evidence": provenance.get("bundle"),
    }


@router.get("/reanalysis/operator-view")
def reanalysis_operator_view(
    tenant_id: str = Depends(get_tenant_from_user),
    user: UserContext = Depends(require_permission("reanalysis:run")),
    store: ClinicalStore = Depends(get_store),
    resolver: EvidenceResolver = Depends(get_resolver),
) -> Dict[str, Any]:
    queue_rows = store.list_reanalysis_queue(tenant_id=tenant_id)
    run_rows = store.list_reanalysis_runs(tenant_id=tenant_id)
    event_rows = store.list_reanalysis_events(tenant_id=tenant_id)
    manifests = [
        {"source": row["name"], "version": row["version"], "ready": bool(row["version"])}
        for row in resolver.provider_catalog
    ]
    return {
        "queue": operator_queue_view(queue_rows),
        "runs": operator_run_manifests(run_rows),
        "same_tier_changes": same_tier_changes(event_rows),
        "provider_cache_readiness": provider_cache_readiness(
            manifests,
            required_sources=resolver.provider_names,
        ),
    }


@router.get("/reanalysis/policy")
def get_reanalysis_policy(
    tenant_id: str = Depends(get_tenant_from_user),
    user: UserContext = Depends(require_permission("reanalysis:run")),
    store: ClinicalStore = Depends(get_store),
) -> Dict[str, Any]:
    return store.get_reanalysis_policy(tenant_id=tenant_id)


@router.post("/reanalysis/policy")
def set_reanalysis_policy(
    req: ReanalysisPolicyRequest,
    tenant_id: str = Depends(get_tenant_from_user),
    user: UserContext = Depends(require_permission("reanalysis:run")),
    store: ClinicalStore = Depends(get_store),
    audit: AuditLog = Depends(get_audit_log),
) -> Dict[str, Any]:
    policy = store.set_reanalysis_policy(tenant_id=tenant_id, policy=req.model_dump())
    audit.append(
        tenant_id=tenant_id,
        actor_id=user.user_id,
        action="reanalysis.policy_update",
        resource_type="tenant",
        resource_id=tenant_id,
        detail={"cadence": policy.get("cadence"), "included_sources": policy.get("included_sources")},
    )
    return policy
