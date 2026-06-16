"""Stateless helpers shared by the routers.

These turn an :class:`api.schemas.EvidenceInput` into the engine's
``EvidenceEvent`` list (resolving through providers when asked) and shape the
classification response so it always carries provenance + versions + the
reconstruction hash. No persistence and no clinical decisions happen here.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, Dict, List, Optional, Tuple

from engine import config as C
from engine.scoring import (
    Classification,
    EvidenceEvent,
    derive_criteria_from_signals,
)

from .evidence_resolver import EvidenceResolver
from .schemas import EvidenceInput, VariantRef


def _event_model_to_engine(ev) -> EvidenceEvent:
    return EvidenceEvent(
        source=ev.source,
        acmg_criterion=ev.acmg_criterion,
        evidence_direction=ev.evidence_direction,
        applied_strength=ev.applied_strength,
        points=ev.points,
        source_version=ev.source_version,
        raw=dict(ev.raw or {}),
    )


def resolve_evidence(
    evidence: EvidenceInput,
    resolver: EvidenceResolver,
    *,
    fallback_variant: Optional[VariantRef] = None,
) -> Tuple[List[EvidenceEvent], Dict[str, Any]]:
    """Resolve an :class:`EvidenceInput` into ``(events, provenance)``.

    ``provenance`` always includes ``warnings`` and ``provider_versions`` and,
    when evidence was resolved through providers, the merged ``EvidenceBundle``
    plus a per-provider breakdown. An empty input yields an empty event list with
    a ``no_evidence_provided`` warning so absence is an explicit, auditable
    outcome rather than an error.
    """
    provenance: Dict[str, Any] = {
        "warnings": [],
        "provider_versions": {},
        "bundle": None,
        "per_provider": {},
    }

    if evidence.events is not None:
        events = [_event_model_to_engine(e) for e in evidence.events]
        if not events:
            provenance["warnings"].append("no_evidence_provided")
        return events, provenance

    if evidence.signals is not None:
        events = derive_criteria_from_signals(dict(evidence.signals))
        if not events:
            provenance["warnings"].append("no_evidence_derived_from_signals")
        return events, provenance

    resolve_req = evidence.resolve
    variant = resolve_req.variant if resolve_req is not None else fallback_variant
    providers = resolve_req.providers if resolve_req is not None else None
    if variant is None:
        provenance["warnings"].append("no_evidence_provided")
        return [], provenance

    result = resolver.resolve(
        variant.to_provider_input(),
        variant_key=variant.variant_key(),
        providers=providers,
    )
    bundle = result["bundle"]
    per_provider = result["per_provider"]
    provenance["bundle"] = bundle.to_dict()
    provenance["per_provider"] = {k: v.to_dict() for k, v in per_provider.items()}
    provenance["provider_versions"] = dict(bundle.provider_versions)
    provenance["warnings"] = list(bundle.warnings)
    if not bundle.events:
        provenance["warnings"].append("no_evidence_resolved")
    return list(bundle.events), provenance


def classification_response(
    clf: Classification,
    provenance: Optional[Dict[str, Any]] = None,
    *,
    receipt: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Assemble the standard classification payload (provenance + versions).

    Always reports the engine version + the tier cutoffs in force and the
    reconstruction hash so a result can be re-derived and audited. When a stored
    ``receipt`` is supplied its sign-off state (and ``is_draft``) is surfaced;
    otherwise the result is a stateless preview that is, by definition, a draft.
    """
    provenance = provenance or {}
    payload: Dict[str, Any] = {
        "classification": asdict(clf),
        "engine_version": clf.engine_version,
        "config": {
            "tier_cutoffs": {
                "pathogenic": 10, "likely_pathogenic": 6, "vus": 0, "likely_benign": -6,
            },
            "strength_points": dict(C.STRENGTH_POINTS),
        },
        "reconstruction_hash": clf.reconstruction_hash,
        "warnings": list(provenance.get("warnings", [])),
        "provider_versions": dict(provenance.get("provider_versions", {})),
        "evidence": provenance.get("bundle"),
        "evidence_by_provider": provenance.get("per_provider", {}),
    }
    if receipt is not None:
        payload["receipt"] = receipt
        payload["is_draft"] = receipt.get("is_draft", True)
        payload["signed_off_by"] = receipt.get("signed_off_by")
    else:
        # A stateless classification has not been signed off; it is not a clinical
        # release. Make that explicit so callers never treat a preview as final.
        payload["is_draft"] = True
        payload["signed_off_by"] = None
    return payload
