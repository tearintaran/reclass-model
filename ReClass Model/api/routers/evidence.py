"""``POST /evidence/resolve`` — resolve provenance-rich evidence for a variant.

Evidence resolution is *de-identified* (it works on a public locus / Variation
ID), so this endpoint is not tenant-scoped: it carries no patient data. It is the
inspectable first step of the workflow — a reviewer can see exactly what each
source returned (events, versions, warnings, identity match) before anything is
classified or persisted.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ..auth import UserContext
from ..authz import require_permission
from ..deps import get_resolver
from ..evidence_resolver import EvidenceResolver
from ..schemas import (
    EvidenceBundleResponse,
    ProviderInfo,
    ProvidersResponse,
    ResolveRequest,
)

router = APIRouter(tags=["evidence"])


@router.get("/evidence/providers", response_model=ProvidersResponse)
def list_providers(
    user: UserContext = Depends(require_permission("evidence:resolve")),
    resolver: EvidenceResolver = Depends(get_resolver),
) -> ProvidersResponse:
    """List the *configured* evidence providers and their source versions.

    De-identified (no patient data, no variant), like resolve. The reviewer UI
    uses this to populate its provider panel without first running a resolve, so
    the available provider set is never hardcoded on the client.
    """
    return ProvidersResponse(
        providers=[ProviderInfo(**p) for p in resolver.provider_catalog],
    )


@router.post("/evidence/resolve", response_model=EvidenceBundleResponse)
def resolve_evidence(
    req: ResolveRequest,
    user: UserContext = Depends(require_permission("evidence:resolve")),
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
        transcript=bundle["transcript"],
        cohort_counts=bundle["cohort_counts"],
        per_provider=per_provider,
    )
