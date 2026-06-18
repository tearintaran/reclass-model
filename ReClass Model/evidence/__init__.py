"""Evidence-integration layer (gap.md Phase 1A).

This package is the first reusable *evidence-provider* slice. It sits between raw
public sources and the pure scoring engine:

    raw source records
      -> EvidenceProvider.fetch(case_or_variant)
      -> EvidenceBundle (standardized engine.scoring.EvidenceEvent list + provenance)
      -> engine.scoring.classify(...)

The engine (`engine.scoring`) stays a PURE function of (evidence, config); all the
I/O, source parsing, identity matching, and provenance bookkeeping live here so the
classification step itself remains exactly reconstructable.

Public surface:

    EvidenceBundle            -- provenance-rich container of EvidenceEvents
    EvidenceProvider          -- fetch(case_or_variant) -> EvidenceBundle contract
    ClinGenIndex              -- ClinGen ERepo records keyed by ClinVar Variation ID
    ClinGenEvidenceProvider   -- provider that recovers VCEP-applied ACMG criteria
"""

from __future__ import annotations

from .model import (
    CohortCounts,
    EvidenceBundle,
    TranscriptIdentity,
    event_from_dict,
    event_to_dict,
)
from .providers import EvidenceProvider
from .clingen import (
    ClinGenEvidenceProvider,
    ClinGenIndex,
    PROVIDER_NAME,
    PROVIDER_VERSION,
    event_to_criterion,
)
from .upstream import (
    CaseControlAdapter,
    DeNovoAdapter,
    DiseaseMechanismAdapter,
    FunctionalAssayAdapter,
    FunctionalPhenotypeCache,
    PhasingAdapter,
    PhenotypeAdapter,
    SegregationAdapter,
    UpstreamEvidenceAdapter,
    UpstreamEvidenceProvider,
    derive_upstream_events,
)

__all__ = [
    "EvidenceBundle",
    "TranscriptIdentity",
    "CohortCounts",
    "EvidenceProvider",
    "ClinGenIndex",
    "ClinGenEvidenceProvider",
    "PROVIDER_NAME",
    "PROVIDER_VERSION",
    "event_to_dict",
    "event_from_dict",
    "event_to_criterion",
    "UpstreamEvidenceAdapter",
    "UpstreamEvidenceProvider",
    "DeNovoAdapter",
    "PhasingAdapter",
    "SegregationAdapter",
    "PhenotypeAdapter",
    "FunctionalAssayAdapter",
    "DiseaseMechanismAdapter",
    "CaseControlAdapter",
    "FunctionalPhenotypeCache",
    "derive_upstream_events",
]
