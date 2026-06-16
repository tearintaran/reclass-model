"""``POST /classify`` — stateless classification preview.

A pure pass-through to ``engine.scoring.classify``: resolve/derive evidence, sum
it into a tier, and return the full provenance + engine version + reconstruction
hash. Nothing is persisted, so the result is always a draft (``is_draft=true``,
no signer) — a clinical release only ever comes from a persisted, signed-off
receipt.
"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends

from engine.scoring import classify

from ..deps import get_resolver
from ..evidence_resolver import EvidenceResolver
from ..schemas import ClassifyRequest
from ..service import classification_response, resolve_evidence

router = APIRouter(tags=["classify"])


@router.post("/classify")
def classify_variant(
    req: ClassifyRequest,
    resolver: EvidenceResolver = Depends(get_resolver),
) -> Dict[str, Any]:
    events, provenance = resolve_evidence(
        req.evidence, resolver, fallback_variant=req.variant
    )
    clf = classify(events)
    return classification_response(clf, provenance)
