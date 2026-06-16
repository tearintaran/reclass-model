"""``POST /evidence/resolve`` — resolve provenance-rich evidence for a variant.

Evidence resolution is *de-identified* (it works on a public locus / Variation
ID), so this endpoint is not tenant-scoped: it carries no patient data. It is the
inspectable first step of the workflow — a reviewer can see exactly what each
source returned (events, versions, warnings, identity match) before anything is
classified or persisted.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ..deps import get_resolver
from ..evidence_resolver import EvidenceResolver
from ..schemas import EvidenceBundleResponse, ResolveRequest

router = APIRouter(tags=["evidence"])


@router.post("/evidence/resolve", response_model=EvidenceBundleResponse)
def resolve_evidence(
    req: ResolveRequest,
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
        per_provider=per_provider,
    )
