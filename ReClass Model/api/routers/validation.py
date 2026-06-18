"""``POST /validation/run`` — development-only concordance/gate check.

This exposes the offline validation harness (spec 12) so a developer can run a
benchmark through the live service. It is gated to ``environment == development``
because it reports cohort-level concordance, not a clinical result, and should
never be reachable in staging/production. It is read-only: it computes metrics
without writing report files or plots.
"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from engine import config as C

from ..auth import UserContext
from ..authz import require_permission
from ..deps import get_app_settings
from ..settings import Settings

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
