"""Extended ACMG/AMP evidence providers (job1 task 4).

The base slice covered ClinGen-applied criteria, REVEL (PP3/BP4), and gnomAD
frequency (BA1/BS1/PM2). This module adds reusable, provenance-rich providers for
the next, highest-value criteria and variant classes, each mapping a raw signal to
the engine's standard :class:`~engine.scoring.EvidenceEvent`s via the thresholds in
``engine/configs/coverage_ext_v1.json``:

  * :class:`Pvs1Provider`           -- loss-of-function consequence -> PVS1
  * :class:`FunctionalAssayProvider`-- well-established assay readout -> PS3 / BS3
  * :class:`InTransPm3Provider`     -- in-trans-with-pathogenic points -> PM3
  * :class:`SegregationProvider`    -- informative meioses -> PP1 / BS4
  * :class:`PhenotypeProvider`      -- phenotype specificity -> PP4
  * :class:`SpliceProvider`         -- SpliceAI delta -> PP3 / BP4 (PVS1 at canonical sites)
  * :class:`CopyNumberProvider`     -- CNV dosage category -> PVS1 / PM4
  * :class:`NonCodingProvider`      -- regulatory/non-coding category -> PM1/PM4/PP3/BP4/BP7
  * :class:`ComplexIndelProvider`   -- multi-base/delins indel -> PVS1 (frameshift) / PM4 (in-frame)
  * :class:`MitochondrialProvider`  -- mtDNA frequency/heteroplasmy -> BA1/BS1/PM2/PS4
  * :class:`RepeatExpansionProvider`-- STR repeat count at a known locus -> PVS1 (expansion)
  * :class:`StructuralVariantProvider` -- breakpoint/dosage-sensitive-gene SV -> PVS1/PM4/BP4/BA1
  * :class:`ExtendedEvidenceProvider` -- runs all of the above and merges their bundles

Each ``fetch`` is deterministic for a fixed config and never raises on "no signal":
it returns an empty-but-valid bundle with a deterministic warning, exactly like the
base providers. The first seven providers were the prioritized base slice (job1 task
4 ordering); the non-coding, complex-indel, mitochondrial, repeat-expansion, and
richer structural-variant providers landed on this same scaffold as gap.md Track A2.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from engine.scoring import (
    EvidenceEvent,
    derive_extended_criteria,
    load_coverage_ext,
    _cnv_to_event,
    _complex_indel_to_event,
    _functional_to_event,
    _mito_to_event,
    _noncoding_to_event,
    _phenotype_to_event,
    _pm3_to_event,
    _pvs1_to_event,
    _repeat_to_event,
    _segregation_to_event,
    _splice_to_event,
    _sv_to_event,
)
from engine.normalize import canonical_key as _canonical_key, locus_from_case

from .model import EvidenceBundle
from .providers import EvidenceProvider

#: Source/provider version string, pinned to the config it scores against.
PROVIDER_VERSION = "coverage_ext_v1"


def _signals_of(case_or_variant: Any) -> Dict[str, Any]:
    """Return the ``signals`` dict for a case, or treat the input as signals itself."""
    if isinstance(case_or_variant, dict):
        if isinstance(case_or_variant.get("signals"), dict):
            return case_or_variant["signals"]
        return case_or_variant
    return {}


def _variant_key_of(case_or_variant: Any) -> Optional[str]:
    """Best-effort canonical key from a case ``locus`` block (None when absent)."""
    loc = locus_from_case(case_or_variant) if isinstance(case_or_variant, dict) else None
    if loc is None:
        return None
    return _canonical_key(*loc)


class _SingleSignalProvider(EvidenceProvider):
    """Base for the one-signal-key extended providers.

    A subclass declares ``signal_key`` (where it reads its raw signal in
    ``signals``) and ``_mapper`` (the scoring function that yields at most one
    :class:`EvidenceEvent`). Everything else -- bundle assembly, the
    ``no_*_signal`` / ``*_not_applicable`` warnings, provenance -- is shared.
    """

    signal_key: str = ""

    def __init__(self, ext: Optional[Dict[str, Any]] = None) -> None:
        self.ext = ext if ext is not None else load_coverage_ext()

    def _mapper(self, signal: Any) -> Optional[EvidenceEvent]:  # pragma: no cover - overridden
        raise NotImplementedError

    def fetch(self, case_or_variant: Any) -> EvidenceBundle:
        pv = {self.name: self.version}
        variant_key = _variant_key_of(case_or_variant)
        signal = _signals_of(case_or_variant).get(self.signal_key)

        if signal is None:
            return EvidenceBundle(
                variant_key=variant_key, events=[], provider_versions=pv,
                source_records=[], warnings=[f"no_{self.signal_key}_signal"],
                match={f"{self.name}_match": False, "criterion": None},
            )

        event = self._mapper(signal)
        if event is None:
            # A present-but-non-actionable signal (e.g. PVS1 on a non-LoF gene,
            # an indeterminate assay): recorded, never silently dropped.
            return EvidenceBundle(
                variant_key=variant_key, events=[], provider_versions=pv,
                source_records=[{"signal": signal}],
                warnings=[f"{self.name}_not_applicable"],
                match={f"{self.name}_match": False, "criterion": None,
                       "signal": signal},
            )

        return EvidenceBundle(
            variant_key=variant_key, events=[event], provider_versions=pv,
            source_records=[{"signal": signal, "raw": dict(event.raw)}],
            warnings=[],
            match={f"{self.name}_match": True, "criterion": event.acmg_criterion,
                   "direction": event.evidence_direction,
                   "strength": event.applied_strength},
        )


class Pvs1Provider(_SingleSignalProvider):
    """Loss-of-function consequence -> PVS1 at the SVI decision-tree strength."""
    name = "pvs1"
    version = PROVIDER_VERSION
    signal_key = "pvs1"

    def _mapper(self, signal: Any) -> Optional[EvidenceEvent]:
        return _pvs1_to_event(signal, self.ext)


class FunctionalAssayProvider(_SingleSignalProvider):
    """Well-established functional assay -> PS3 (damaging) or BS3 (normal)."""
    name = "functional_assay"
    version = PROVIDER_VERSION
    signal_key = "functional"

    def _mapper(self, signal: Any) -> Optional[EvidenceEvent]:
        return _functional_to_event(signal, self.ext)


class InTransPm3Provider(_SingleSignalProvider):
    """In-trans-with-pathogenic observations -> PM3 (SVI point system)."""
    name = "in_trans"
    version = PROVIDER_VERSION
    signal_key = "pm3"

    def _mapper(self, signal: Any) -> Optional[EvidenceEvent]:
        return _pm3_to_event(signal, self.ext)


class SegregationProvider(_SingleSignalProvider):
    """Informative meioses -> PP1 (cosegregation) or BS4 (non-segregation)."""
    name = "segregation"
    version = PROVIDER_VERSION
    signal_key = "segregation"

    def _mapper(self, signal: Any) -> Optional[EvidenceEvent]:
        return _segregation_to_event(signal, self.ext)


class PhenotypeProvider(_SingleSignalProvider):
    """Phenotype specificity -> PP4 (supporting/moderate)."""
    name = "phenotype"
    version = PROVIDER_VERSION
    signal_key = "phenotype"

    def _mapper(self, signal: Any) -> Optional[EvidenceEvent]:
        return _phenotype_to_event(signal, self.ext)


class SpliceProvider(_SingleSignalProvider):
    """SpliceAI-style delta -> PP3 / BP4, or PVS1 at a canonical ±1,2 site."""
    name = "splice"
    version = PROVIDER_VERSION
    signal_key = "splice"

    def _mapper(self, signal: Any) -> Optional[EvidenceEvent]:
        return _splice_to_event(signal, self.ext)


class CopyNumberProvider(_SingleSignalProvider):
    """CNV dosage category -> PVS1 (haploinsufficient loss) / PM4 (partial/gain)."""
    name = "cnv"
    version = PROVIDER_VERSION
    signal_key = "cnv"

    def _mapper(self, signal: Any) -> Optional[EvidenceEvent]:
        return _cnv_to_event(signal, self.ext)


class NonCodingProvider(_SingleSignalProvider):
    """Non-coding / regulatory category -> PM1/PM4/PP3/BP4/BP7 (gap.md A2)."""
    name = "noncoding"
    version = PROVIDER_VERSION
    signal_key = "noncoding"

    def _mapper(self, signal: Any) -> Optional[EvidenceEvent]:
        return _noncoding_to_event(signal, self.ext)


class ComplexIndelProvider(_SingleSignalProvider):
    """Multi-base / delins indel -> PVS1 (frameshift, LoF) / PM4 (in-frame) (A2)."""
    name = "complex_indel"
    version = PROVIDER_VERSION
    signal_key = "complex_indel"

    def _mapper(self, signal: Any) -> Optional[EvidenceEvent]:
        return _complex_indel_to_event(signal, self.ext)


class MitochondrialProvider(_SingleSignalProvider):
    """mtDNA-specific frequency / heteroplasmy -> BA1/BS1/PM2/PS4 (A2)."""
    name = "mito"
    version = PROVIDER_VERSION
    signal_key = "mito"

    def _mapper(self, signal: Any) -> Optional[EvidenceEvent]:
        return _mito_to_event(signal, self.ext)


class RepeatExpansionProvider(_SingleSignalProvider):
    """Structured repeat count at a known STR locus -> PVS1 expansion call (A2)."""
    name = "repeat"
    version = PROVIDER_VERSION
    signal_key = "repeat"

    def _mapper(self, signal: Any) -> Optional[EvidenceEvent]:
        return _repeat_to_event(signal, self.ext)


class StructuralVariantProvider(_SingleSignalProvider):
    """Richer SV (breakpoint / dosage-sensitive gene) -> PVS1/PM4/BP4/BA1 (A2)."""
    name = "sv"
    version = PROVIDER_VERSION
    signal_key = "sv"

    def _mapper(self, signal: Any) -> Optional[EvidenceEvent]:
        return _sv_to_event(signal, self.ext)


#: All single-signal extended providers, in job1 task-4 / gap.md A2 priority order.
EXTENDED_PROVIDER_CLASSES = (
    Pvs1Provider,
    FunctionalAssayProvider,
    InTransPm3Provider,
    SegregationProvider,
    PhenotypeProvider,
    SpliceProvider,
    CopyNumberProvider,
    NonCodingProvider,
    ComplexIndelProvider,
    MitochondrialProvider,
    RepeatExpansionProvider,
    StructuralVariantProvider,
)


class ExtendedEvidenceProvider(EvidenceProvider):
    """Run every extended provider over a case and merge into one EvidenceBundle.

    Convenience for callers that want all extended criteria at once. The merged
    bundle is identical (same events, in config order) to
    :func:`engine.scoring.derive_extended_criteria`, with the per-provider warnings
    and source records preserved so a reviewer can see which signals were present,
    actionable, or non-applicable.
    """
    name = "coverage_ext"
    version = PROVIDER_VERSION

    def __init__(self, ext: Optional[Dict[str, Any]] = None) -> None:
        self.ext = ext if ext is not None else load_coverage_ext()
        self._providers = [cls(self.ext) for cls in EXTENDED_PROVIDER_CLASSES]

    def fetch(self, case_or_variant: Any) -> EvidenceBundle:
        variant_key = _variant_key_of(case_or_variant)
        events: List[EvidenceEvent] = []
        source_records: List[Dict[str, Any]] = []
        warnings: List[str] = []
        matched: List[str] = []
        provider_versions = {self.name: self.version}

        for prov in self._providers:
            bundle = prov.fetch(case_or_variant)
            events.extend(bundle.events)
            source_records.extend(bundle.source_records)
            warnings.extend(bundle.warnings)
            provider_versions.update(bundle.provider_versions)
            if bundle.events:
                matched.append(prov.name)

        # Keep the canonical merged event order identical to the scoring helper.
        merged = derive_extended_criteria(_signals_of(case_or_variant), self.ext)
        return EvidenceBundle(
            variant_key=variant_key,
            events=merged if merged else events,
            provider_versions=provider_versions,
            source_records=source_records,
            warnings=warnings,
            match={"coverage_ext_match": bool(matched),
                   "criteria": [e.acmg_criterion for e in (merged or events)],
                   "providers_matched": matched},
        )
