"""ClinGen ERepo evidence provider, keyed by ClinVar Variation ID.

The ClinGen Evidence Repository publishes, per variant, the exact ACMG/AMP criteria
an expert panel (VCEP) applied. The local `clingen_real_v1` fixture already carries
those criteria *and* each record's ClinVar Variation ID. ClinVar cases carry the
same Variation ID, so a direct integer-ID join recovers expert-applied criteria for
the ClinVar benchmark -- the single highest-value evidence-integration step (gap.md
Phase 1A: ~10,649 of 21,638 ClinVar cases match directly).

This module provides:

  * :class:`ClinGenIndex`            -- records keyed by ClinVar Variation ID, with a
                                        deterministic duplicate-resolution policy,
  * :class:`ClinGenEvidenceProvider` -- an :class:`~evidence.providers.EvidenceProvider`
                                        that returns a provenance-rich EvidenceBundle.

Both are pure given a fixed fixture snapshot.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from engine.scoring import EvidenceEvent, derive_criteria_from_signals
from engine.normalize import locus_from_case, normalize_case
from engine.reference import ReferenceProvider

from .model import EvidenceBundle
from .providers import EvidenceProvider

#: Stable provider identity / source version (gap.md Phase 1B).
PROVIDER_NAME = "clingen_erepo"
PROVIDER_VERSION = "ERepo"

# ClinGen rows without a real ClinVar Variation ID carry a sentinel ("-") rather
# than a blank. Treat these (and other empties) as "no ID" so the 1,368 sentinel
# rows never collapse into one bogus, heavily-duplicated join key.
_INVALID_IDS = {"", "-", ".", "na", "n/a", "none", "null"}


def _norm_id(value: Any) -> str:
    return str(value if value is not None else "").strip()


def is_valid_variation_id(value: Any) -> bool:
    """True if ``value`` is a usable ClinVar Variation ID (not a missing sentinel)."""
    return _norm_id(value).lower() not in _INVALID_IDS


def _criteria_of(record: Dict[str, Any]) -> List[Dict[str, Any]]:
    return (record.get("signals") or {}).get("criteria", []) or []


def applied_criteria(record: Dict[str, Any]) -> List[Dict[str, Any]]:
    """A provenance-tagged copy of ``record``'s ClinGen criteria, ready to append.

    Each returned criterion preserves its original ``criterion/direction/strength/
    source/version`` fields (so the engine maps it identically) and gains a ``raw``
    provenance block naming the provider and the originating ClinGen case id. ``raw``
    is intentionally outside the engine's reconstruction hash, so tagging provenance
    never changes a classification -- it only makes it auditable.
    """
    case_id = record.get("id")
    out: List[Dict[str, Any]] = []
    for c in _criteria_of(record):
        c2 = dict(c)
        raw = dict(c2.get("raw") or {})
        raw.setdefault("provider", PROVIDER_NAME)
        raw.setdefault("clingen_case_id", case_id)
        c2["raw"] = raw
        out.append(c2)
    return out


def event_to_criterion(event: EvidenceEvent) -> Dict[str, Any]:
    """Serialize an EvidenceEvent back into a fixture ``signals.criteria`` entry."""
    crit: Dict[str, Any] = {
        "criterion": event.acmg_criterion,
        "direction": event.evidence_direction,
        "strength": event.applied_strength,
        "source": event.source,
        "version": event.source_version,
    }
    if event.points is not None:
        crit["points"] = event.points
    if event.raw:
        crit["raw"] = dict(event.raw)
    return crit


class ClinGenIndex:
    """ClinGen ERepo records indexed by ClinVar Variation ID.

    A single Variation ID can map to more than one ERepo record (e.g. two VCEP
    submissions). :meth:`resolve` picks ONE deterministically.
    """

    def __init__(
        self,
        by_id: Dict[str, List[Dict[str, Any]]],
        skipped_invalid_id: int = 0,
        by_canonical_key: Optional[Dict[str, List[Dict[str, Any]]]] = None,
    ) -> None:
        self._by_id = by_id
        #: Count of source records dropped for lacking a valid Variation ID.
        self.skipped_invalid_id = skipped_invalid_id
        #: Secondary index: build-stripped canonical provider key -> records. Built
        #: from any ClinGen record that carries a `locus` block, so a ClinVar case
        #: with coordinates but no usable Variation ID can still be matched.
        self._by_key: Dict[str, List[Dict[str, Any]]] = by_canonical_key or {}

    # -- construction ------------------------------------------------------- #
    @classmethod
    def from_cases(cls, cases: List[Dict[str, Any]]) -> "ClinGenIndex":
        by_id: Dict[str, List[Dict[str, Any]]] = {}
        by_key: Dict[str, List[Dict[str, Any]]] = {}
        skipped = 0
        for case in cases:
            cid = _norm_id((case.get("provenance") or {}).get("clinvar_id"))
            if is_valid_variation_id(cid):
                by_id.setdefault(cid, []).append(case)
            else:
                skipped += 1
            # A ClinGen record may carry coordinates; index it by canonical key too
            # (reference-free is enough to key an SNV; indels are flagged downstream).
            if locus_from_case(case) is not None:
                nr = normalize_case(case)
                if nr.ok and not nr.blocking:
                    by_key.setdefault(nr.provider_key, []).append(case)
        return cls(by_id, skipped_invalid_id=skipped, by_canonical_key=by_key)

    @classmethod
    def from_fixture(cls, path: str) -> "ClinGenIndex":
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return cls.from_cases(data.get("cases", []))

    # -- inspection --------------------------------------------------------- #
    def __len__(self) -> int:
        return len(self._by_id)

    def __contains__(self, variation_id: Any) -> bool:
        return _norm_id(variation_id) in self._by_id

    @property
    def variation_ids(self) -> set:
        return set(self._by_id)

    @property
    def duplicate_ids(self) -> List[str]:
        """Variation IDs that map to more than one ClinGen record."""
        return sorted(k for k, v in self._by_id.items() if len(v) > 1)

    @property
    def canonical_keys(self) -> set:
        """Build-stripped canonical provider keys present in the secondary index."""
        return set(self._by_key)

    def candidates(self, variation_id: Any) -> List[Dict[str, Any]]:
        """All ClinGen records for a Variation ID (possibly empty)."""
        return list(self._by_id.get(_norm_id(variation_id), []))

    def candidates_by_key(self, provider_key: Optional[str]) -> List[Dict[str, Any]]:
        """All ClinGen records for a build-stripped canonical provider key."""
        if not provider_key:
            return []
        return list(self._by_key.get(provider_key, []))

    # -- deterministic resolution ------------------------------------------- #
    @staticmethod
    def _sort_key(record: Dict[str, Any], expected_tier: Optional[str]) -> tuple:
        """Deterministic duplicate-resolution order (gap.md Phase 1A):

        1. prefer a record whose tier equals the ClinVar case's expected tier,
        2. then prefer the record with MORE applied criteria,
        3. then sort by stable ClinGen case id.
        """
        tier_match = 0 if (
            expected_tier is not None and record.get("expected") == expected_tier
        ) else 1
        return (tier_match, -len(_criteria_of(record)), _norm_id(record.get("id")))

    def resolve(
        self, variation_id: Any, expected_tier: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Pick exactly one ClinGen record for ``variation_id``, or None."""
        cands = self._by_id.get(_norm_id(variation_id))
        if not cands:
            return None
        return min(cands, key=lambda r: self._sort_key(r, expected_tier))

    def resolve_by_key(
        self, provider_key: Optional[str], expected_tier: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Pick exactly one ClinGen record for a canonical provider key, or None."""
        cands = self.candidates_by_key(provider_key)
        if not cands:
            return None
        return min(cands, key=lambda r: self._sort_key(r, expected_tier))


class ClinGenEvidenceProvider(EvidenceProvider):
    """Recover VCEP-applied ACMG criteria for a ClinVar case via Variation ID."""

    name = PROVIDER_NAME
    version = PROVIDER_VERSION

    def __init__(
        self, index: ClinGenIndex, reference: Optional[ReferenceProvider] = None
    ) -> None:
        self.index = index
        #: Optional GRCh38 reference for reference-backed indel normalization when
        #: falling back to canonical-key matching. SNV keys never need it.
        self.reference = reference

    @classmethod
    def from_fixture(
        cls, path: str, reference: Optional[ReferenceProvider] = None
    ) -> "ClinGenEvidenceProvider":
        return cls(ClinGenIndex.from_fixture(path), reference=reference)

    @staticmethod
    def _extract(case_or_variant: Any) -> tuple:
        """Pull (variation_id, expected_tier, variant_key) from a ClinVar case.

        Accepts either a full fixture case dict or a bare Variation ID string/int.
        """
        if isinstance(case_or_variant, dict):
            case = case_or_variant
            vid = _norm_id((case.get("provenance") or {}).get("variation_id"))
            expected = case.get("expected")
            variant_key = "clinvar_variation_id:" + vid if vid else None
            return vid, expected, variant_key
        vid = _norm_id(case_or_variant)
        return vid, None, ("clinvar_variation_id:" + vid if vid else None)

    def fetch(self, case_or_variant: Any) -> EvidenceBundle:
        variation_id, expected_tier, variant_key = self._extract(case_or_variant)
        provider_versions = {self.name: self.version}
        candidates = self.index.candidates(variation_id)

        # 1) Primary join: direct ClinVar Variation ID.
        if is_valid_variation_id(variation_id) and candidates:
            chosen = self.index.resolve(variation_id, expected_tier)
            criteria = applied_criteria(chosen)
            events = derive_criteria_from_signals({"criteria": criteria})

            warnings: List[str] = []
            if len(candidates) > 1:
                warnings.append("multiple_clingen_matches")
            if expected_tier is not None and chosen.get("expected") != expected_tier:
                warnings.append("label_disagreement")
            if not criteria:
                warnings.append("missing_criteria")

            match = {
                "clingen_variation_id_match": True,
                "match_type": "variation_id",
                "variation_id": variation_id,
                "clingen_case_id": chosen.get("id"),
                "clingen_expected": chosen.get("expected"),
                "clinvar_expected": expected_tier,
                "candidate_count": len(candidates),
                "candidate_ids": sorted(_norm_id(c.get("id")) for c in candidates),
            }
            return EvidenceBundle(
                variant_key=variant_key,
                events=events,
                provider_versions=provider_versions,
                source_records=[dict(c) for c in candidates],
                warnings=warnings,
                match=match,
            )

        # 2) Fallback join: canonical variant key, when a Variation ID match was not
        #    available. Returns a bundle (match, or a *blocking* normalization
        #    failure) when the case carries a locus; None to fall through.
        fallback = self._canonical_fallback(
            case_or_variant, expected_tier, provider_versions
        )
        if fallback is not None:
            return fallback

        # 3) True no-match. Preserve the historical warnings exactly.
        base_warning = (
            "no_clingen_match" if is_valid_variation_id(variation_id)
            else "missing_variation_id"
        )
        return EvidenceBundle(
            variant_key=variant_key,
            events=[],
            provider_versions=provider_versions,
            source_records=[],
            warnings=[base_warning],
            match={"clingen_variation_id_match": False, "match_type": "none",
                   "variation_id": variation_id},
        )

    def _canonical_fallback(
        self,
        case_or_variant: Any,
        expected_tier: Optional[str],
        provider_versions: Dict[str, str],
    ) -> Optional[EvidenceBundle]:
        """Attempt a canonical-key join. None => no locus / no canonical candidate.

        A locus that fails to normalize (blocking) returns a bundle whose match
        records ``normalization_failed`` rather than letting the join silently read
        as a clean non-match (acceptance criterion A).
        """
        if locus_from_case(case_or_variant) is None:
            return None  # bare id or no coordinates -> nothing to canonicalize.

        nr = normalize_case(case_or_variant, reference=self.reference)
        if not nr.ok or nr.blocking:
            # Never silently treat a failed normalization as a valid non-match.
            return EvidenceBundle(
                variant_key=None,
                events=[],
                provider_versions=provider_versions,
                source_records=[],
                warnings=list(nr.warnings) or ["normalization_failed"],
                match={"clingen_variation_id_match": False, "match_type": "none",
                       "normalized": False, "warnings": list(nr.warnings)},
            )

        cands = self.index.candidates_by_key(nr.provider_key)
        if not cands:
            return None  # normalized fine, but ClinGen has no record at this key.

        chosen = self.index.resolve_by_key(nr.provider_key, expected_tier)
        criteria = applied_criteria(chosen)
        events = derive_criteria_from_signals({"criteria": criteria})

        warnings = []
        if len(cands) > 1:
            warnings.append("multiple_clingen_matches")
        if expected_tier is not None and chosen.get("expected") != expected_tier:
            warnings.append("label_disagreement")
        if not criteria:
            warnings.append("missing_criteria")
        # Carry the advisory indel flag through so a non-canonical indel key is visible.
        warnings.extend(w for w in nr.warnings if w not in warnings)

        match = {
            "clingen_variation_id_match": False,
            "match_type": "canonical_key",
            "canonical_key": nr.key,
            "provider_key": nr.provider_key,
            "normalization_method": nr.method,
            "clingen_case_id": chosen.get("id"),
            "clingen_expected": chosen.get("expected"),
            "clinvar_expected": expected_tier,
            "candidate_count": len(cands),
            "candidate_ids": sorted(_norm_id(c.get("id")) for c in cands),
        }
        return EvidenceBundle(
            variant_key=nr.key,
            events=events,
            provider_versions=provider_versions,
            source_records=[dict(c) for c in cands],
            warnings=warnings,
            match=match,
        )
