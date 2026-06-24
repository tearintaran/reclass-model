"""``POST /validation/run`` — development-only concordance/gate check.

This exposes the offline validation harness (spec 12) so a developer can run a
benchmark through the live service. It is gated to ``environment == development``
because it reports cohort-level concordance, not a clinical result, and should
never be reachable in staging/production. It is read-only: it computes metrics
without writing report files or plots.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from engine import config as C
from validation.release_gate import evaluate_release_gate
from validation.release_packet import build_release_validation_packet

from ..auth import UserContext
from ..authz import require_permission
from ..deps import get_app_settings, get_audit_log, get_store, get_tenant_from_user
from ..schemas import (
    ReleaseApprovalRequest,
    ReleaseGateRequest,
    ReleasePacketRequest,
    ReleaseStateRequest,
)
from ..settings import Settings, preflight_check
from ..store import ClinicalStore
from ..audit import AuditLog

router = APIRouter(tags=["validation"])


class ValidationRunRequest(BaseModel):
    benchmark: str = "synthetic_v1"


@router.post("/validation/run")
def run_validation(
    req: ValidationRunRequest,
    settings: Settings = Depends(get_app_settings),
    user: UserContext = Depends(require_permission("validation:run")),
) -> Dict[str, Any]:
    if not settings.is_development:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="validation endpoint is available in development environments only",
        )
    from validation import harness

    try:
        benchmark = harness.load_benchmark(req.benchmark)
    except SystemExit as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))

    results = harness.evaluate(benchmark)
    metrics = harness.compute_metrics(results)
    passed = harness.gate_passes(metrics)
    return {
        "benchmark": benchmark.get("benchmark", req.benchmark),
        "engine_version": C.ENGINE_VERSION,
        "gate_pass": passed,
        "metrics": metrics,
    }


@router.post("/validation/release-gate")
def release_gate_preview(
    req: ReleaseGateRequest,
    user: UserContext = Depends(require_permission("validation:run")),
) -> Dict[str, Any]:
    fp = C.config_fingerprint()
    result = evaluate_release_gate(
        classification=req.classification or {},
        signoff_packet=req.signoff_packet,
        current_state=req.current_state,
        target_scope=req.target_scope,
        active_config_hash=req.active_config_hash or fp.get("config_hash"),
        preflight_failures=req.preflight_failures,
        serious_discordances=req.serious_discordances,
    )
    return result.to_dict()


@router.get("/validation/release-gate/{classification_id}")
def release_gate_status(
    classification_id: str,
    tenant_id: str = Depends(get_tenant_from_user),
    user: UserContext = Depends(require_permission("validation:run")),
    store: ClinicalStore = Depends(get_store),
    settings: Settings = Depends(get_app_settings),
) -> Dict[str, Any]:
    receipt = store.get_classification(tenant_id=tenant_id, classification_id=classification_id)
    if receipt is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="classification not found")
    failures = [
        failure.to_dict()
        for failure in preflight_check(settings, base_path=Path(__file__).resolve().parents[2])
    ]
    result = evaluate_release_gate(
        classification=receipt,
        signoff_packet=receipt.get("signoff_packet") or {},
        current_state=receipt.get("release_state") or "review_pending",
        active_config_hash=C.config_fingerprint().get("config_hash"),
        preflight_failures=failures,
    )
    return result.to_dict()


@router.post("/validation/release-gate/{classification_id}/approve")
def approve_release(
    classification_id: str,
    req: ReleaseApprovalRequest,
    tenant_id: str = Depends(get_tenant_from_user),
    user: UserContext = Depends(require_permission("validation:run")),
    store: ClinicalStore = Depends(get_store),
    settings: Settings = Depends(get_app_settings),
    audit: AuditLog = Depends(get_audit_log),
) -> Dict[str, Any]:
    receipt = store.get_classification(tenant_id=tenant_id, classification_id=classification_id)
    if receipt is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="classification not found")
    failures = [
        failure.to_dict()
        for failure in preflight_check(settings, base_path=Path(__file__).resolve().parents[2])
    ]
    result = evaluate_release_gate(
        classification=receipt,
        signoff_packet=req.signoff_packet,
        current_state=receipt.get("release_state") or "review_pending",
        target_scope=req.target_scope,
        active_config_hash=C.config_fingerprint().get("config_hash"),
        preflight_failures=failures,
        serious_discordances=req.serious_discordances,
    )
    if not result.passed:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=result.to_dict(),
        )
    approved = store.approve_release(
        tenant_id=tenant_id,
        classification_id=classification_id,
        signoff_packet=req.signoff_packet,
    )
    audit.append(
        tenant_id=tenant_id,
        actor_id=user.user_id,
        action="classification.release_approved",
        resource_type="classification",
        resource_id=classification_id,
        detail={"release_state": approved.get("release_state")},
    )
    return approved


@router.post("/validation/release-gate/{classification_id}/state")
def transition_release_state_endpoint(
    classification_id: str,
    req: ReleaseStateRequest,
    tenant_id: str = Depends(get_tenant_from_user),
    user: UserContext = Depends(require_permission("validation:run")),
    store: ClinicalStore = Depends(get_store),
    audit: AuditLog = Depends(get_audit_log),
) -> Dict[str, Any]:
    try:
        updated = store.transition_release_state(
            tenant_id=tenant_id,
            classification_id=classification_id,
            next_state=req.state,
            release_notes=req.release_notes,
        )
    except LookupError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="classification not found")
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    audit.append(
        tenant_id=tenant_id,
        actor_id=user.user_id,
        action="classification.release_state_change",
        resource_type="classification",
        resource_id=classification_id,
        detail={"new_state": req.state},
    )
    return updated


@router.post("/validation/release-packet")
def release_packet_preview(
    req: ReleasePacketRequest,
    user: UserContext = Depends(require_permission("validation:run")),
) -> Dict[str, Any]:
    return build_release_validation_packet(
        release_scope=req.release_scope,
        config_hash=req.config_hash or C.config_fingerprint().get("config_hash"),
        source_snapshots=req.source_snapshots,
        benchmark_metrics=req.benchmark_metrics,
        serious_discordances=req.serious_discordances,
        sign_off_ledger=req.sign_off_ledger,
        validation_report_id=req.validation_report_id,
    )


@router.get("/validation/release-packet/{classification_id}")
def release_packet_for_classification(
    classification_id: str,
    tenant_id: str = Depends(get_tenant_from_user),
    user: UserContext = Depends(require_permission("validation:run")),
    store: ClinicalStore = Depends(get_store),
) -> Dict[str, Any]:
    receipt = store.get_classification(tenant_id=tenant_id, classification_id=classification_id)
    if receipt is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="classification not found")
    scope = receipt.get("release_scope") or receipt.get("signoff_packet", {}).get("clinical_scope") or {}
    ledger = store.release_signoff_ledger(
        tenant_id=tenant_id,
        classification_id=classification_id,
    )
    return build_release_validation_packet(
        release_scope=scope,
        config_hash=receipt.get("config_hash") or C.config_fingerprint().get("config_hash"),
        source_snapshots=receipt.get("source_snapshots") or {},
        sign_off_ledger=ledger,
        validation_report_id=receipt.get("validation_report_id"),
    )
