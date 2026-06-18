"""ClinGen ERepo evidence provider, keyed by ClinVar Variation ID.

The ClinGen Evidence Repository publishes, per variant, the exact ACMG/AMP criteria
an expert panel (VCEP) applied. The local `clingen_real_v1` fixture already carries
those criteria *and* each record's ClinVar Variation ID. ClinVar cases carry the
same Variation ID, so a direct integer-ID join recovers expert-applied criteria for
the ClinVar benchmark -- the single highest-value evidence-integration step (gap.md
Phase 1A: ~10,649 of 21,638 ClinVar cases match directly).

This module provides:

  * :class:`ClinGenIndex`            -- records keyed by ClinVar Variation ID *and* by
                                        canonical variant key, with a deterministic
                                        duplicate-resolution policy,
  * :class:`ClinGenEvidenceProvider` -- an :class:`~evidence.providers.EvidenceProvider`
                                        that returns a provenance-rich EvidenceBundle.

When the direct Variation ID join misses, the provider falls back through strictly
weaker tiers (job1), and a weaker tier never overrides a stronger one (``ROUTE_PRIORITY``):

  1. ``variation_id``            -- direct ClinVar Variation ID,
  2. ``canonical_snv``          -- canonical coordinate / SNV-MNV key (reference-free),
  3. ``reference_backed_indel`` -- left-aligned indel from a native coordinate locus,
  4. ``hgvs_g``                 -- indel recovered from genomic HGVS (resolved against
                                   the reference; ClinGen carries the genomic token).

A fallback key that maps to multiple, non-criteria-equivalent records is treated as
*ambiguous*: nothing is imported, a deterministic warning is emitted, and the
candidate detail is preserved (the no-match is never treated as benign).

Both are pure given a fixed fixture snapshot (and, for the ``hgvs_g`` tier, a fixed
reference genome).
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from engine.scoring import EvidenceEvent, derive_criteria_from_signals
from engine.normalize import (
    locus_from_case,
    normalize_case,
    normalize_locus,
    transcript_hgvs_key,
)
from engine.reference import ReferenceProvider
from ingest.hgvs import locus_from_genomic_hgvs, parse_spdi

from .model import EvidenceBundle, TranscriptIdentity
from .providers import EvidenceProvider

#: Stable provider identity / source version (gap.md Phase 1B).
PROVIDER_NAME = "clingen_erepo"
PROVIDER_VERSION = "ERepo"

# Strict match-route priority (job1): a weaker route must NEVER override a stronger
# one. Lower number == stronger. ``variation_id`` is the primary join; the rest are
# fallback tiers attempted, in order, only when the variation-ID join misses. job1
# task 3 adds ``clinvar_allele_id`` (allele-precise ClinVar identity), ``spdi`` (NCBI
# SPDI resolved to a canonical genomic key), and makes ``hgvs_c_mane`` buildable as a
# MANE-transcript + coding-HGVS identity route.
ROUTE_PRIORITY = {
    "variation_id": 1,          # direct ClinVar Variation ID
    "clinvar_allele_id": 2,     # ClinVar Allele ID (allele-precise)
    "canonical_snv": 3,         # canonical coordinate / canonical SNV-MNV key
    "reference_backed_indel": 4,  # left-aligned indel from a native coordinate locus
    "hgvs_g": 5,                # indel recovered from genomic HGVS (+ reference)
    "spdi": 6,                  # NCBI SPDI resolved to a canonical genomic key
    "hgvs_c_mane": 7,           # MANE-transcript coding c.HGVS identity
    "hgvs_p_gene": 8,           # gene + protein p.HGVS        (not buildable -- see enrich)
    "source_synonym": 9,        # other source synonym         (not buildable -- see enrich)
}

# ClinGen rows without a real ClinVar Variation ID carry a sentinel ("-") rather
# than a blank. Treat these (and other empties) as "no ID" so the 1,368 sentinel
# rows never collapse into one bogus, heavily-duplicated join key.
_INVALID_IDS = {"", "-", ".", "na", "n/a", "none", "null"}


def _norm_id(value: Any) -> str:
    return str(value if value is not None else "").strip()


def is_valid_variation_id(value: Any) -> bool:
    """True if ``value`` is a usable ClinVar Variation ID (not a missing sentinel)."""
    return _norm_id(value).lower() not in _INVALID_IDS


def _provenance_field(obj: Any, *names: str) -> Optional[str]:
    """First non-empty value of ``names`` from ``obj.provenance`` or ``obj`` top level."""
    if not isinstance(obj, dict):
        return None
    prov = obj.get("provenance") or {}
    for name in names:
        for src in (prov, obj):
            v = src.get(name)
            if v not in (None, ""):
                return v
    return None


def allele_id_of(obj: Any) -> Optional[str]:
    """Usable ClinVar Allele ID for a case/record (job1 task 3), or None.

    Read from ``provenance.allele_id`` (or a top-level ``allele_id``). Sentinel /
    blank values are rejected via :func:`is_valid_variation_id` so the 1,368-style
    missing-ID rows never collapse into one bogus join key.
    """
    aid = _norm_id(_provenance_field(obj, "allele_id", "clinvar_allele_id"))
    return aid if is_valid_variation_id(aid) else None


def spdi_of(obj: Any) -> Optional[str]:
    """NCBI SPDI expression for a case/record (job1 task 3), or None."""
    s = _provenance_field(obj, "spdi")
    return str(s).strip() if s else None


def transcript_key_of(obj: Any) -> Optional[str]:
    """MANE-transcript + coding-HGVS identity key for a case/record (job1 task 3/4).

    Prefers a structured ``transcript`` block (``mane_select`` / ``refseq`` +
    ``hgvs_c``), then provenance fields. Returns the version-stripped
    ``transcript:c.HGVS`` key, or None when no usable transcript identity is present.
    """
    tx = obj.get("transcript") if isinstance(obj, dict) else None
    tx = tx if isinstance(tx, dict) else {}
    transcript = (
        tx.get("mane_select") or tx.get("refseq")
        or _provenance_field(obj, "mane_transcript", "refseq_transcript", "transcript")
    )
    hgvs_c = tx.get("hgvs_c") or _provenance_field(obj, "hgvs_c")
    return transcript_hgvs_key(transcript, hgvs_c)


def _criteria_of(record: Dict[str, Any]) -> List[Dict[str, Any]]:
    return (record.get("signals") or {}).get("criteria", []) or []


def _criteria_signature(record: Dict[str, Any]) -> frozenset:
    """The set of (criterion, direction, strength) a record would import.

    Two records are *deterministically equivalent for the criteria being imported*
    (job1 ambiguity rule) iff their signatures are equal -- provenance/raw differ but
    the engine sees the identical evidence, so picking either yields the same tier.
    """
    return frozenset(
        (c.get("criterion"), c.get("direction"), c.get("strength"))
        for c in _criteria_of(record)
    )


def records_criteria_equivalent(records: List[Dict[str, Any]]) -> bool:
    """True when every record imports the identical criteria set (so a multi-record
    fallback key is safe to enrich from any one of them)."""
    return len({_criteria_signature(r) for r in records}) <= 1


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
        key_route: Optional[Dict[str, str]] = None,
        by_allele_id: Optional[Dict[str, List[Dict[str, Any]]]] = None,
        by_transcript_key: Optional[Dict[str, List[Dict[str, Any]]]] = None,
    ) -> None:
        self._by_id = by_id
        #: Count of source records dropped for lacking a valid Variation ID.
        self.skipped_invalid_id = skipped_invalid_id
        #: Secondary index: build-stripped canonical provider key -> records. Built
        #: from any ClinGen record that carries a `locus` block OR a GRCh38 genomic
        #: HGVS token (indels, resolved with a reference), so a ClinVar case with
        #: coordinates but no usable Variation ID can still be matched.
        self._by_key: Dict[str, List[Dict[str, Any]]] = by_canonical_key or {}
        #: provider key -> the fallback route that produced it (strongest wins). One
        #: of ``canonical_snv`` / ``reference_backed_indel`` / ``hgvs_g``.
        self._key_route: Dict[str, str] = key_route or {}
        #: Secondary index: ClinVar Allele ID -> records (job1 task 3). Built from any
        #: ClinGen record carrying a valid `provenance.allele_id`.
        self._by_allele_id: Dict[str, List[Dict[str, Any]]] = by_allele_id or {}
        #: Secondary index: MANE-transcript + coding-HGVS identity key -> records
        #: (job1 task 3). Built from any record carrying a transcript identity.
        self._by_transcript_key: Dict[str, List[Dict[str, Any]]] = by_transcript_key or {}

    # -- construction ------------------------------------------------------- #
    @staticmethod
    def _record_key_route(
        record: Dict[str, Any], reference: Optional[ReferenceProvider]
    ) -> tuple:
        """Best canonical (provider_key, route) for one ClinGen record, or (None, None).

        A ``locus`` block keys directly: SNV/MNV -> ``canonical_snv`` (reference-free),
        indel -> ``reference_backed_indel`` (left-aligned when a reference is supplied).
        Otherwise a GRCh38 genomic HGVS token in ``provenance.grch38_hgvs`` is resolved
        against the reference -- an indel becomes ``hgvs_g`` (job1 task 1), an SNV stays
        ``canonical_snv``. Reference-free callers cannot resolve indel HGVS, so those
        records simply stay out of the canonical index (honest miss, never a guess).
        """
        if locus_from_case(record) is not None:
            nr = normalize_case(record, reference=reference)
            if nr.ok and not nr.blocking:
                route = "reference_backed_indel" if nr.is_indel else "canonical_snv"
                return nr.provider_key, route
            return None, None

        hgvs = (record.get("provenance") or {}).get("grch38_hgvs")
        if reference is not None and hgvs:
            loc = locus_from_genomic_hgvs(hgvs, reference)
            if loc is not None:
                nr = normalize_locus(*loc, reference=reference)
                if nr.ok and not nr.blocking:
                    route = "hgvs_g" if nr.is_indel else "canonical_snv"
                    return nr.provider_key, route
        return None, None

    @classmethod
    def from_cases(
        cls,
        cases: List[Dict[str, Any]],
        reference: Optional[ReferenceProvider] = None,
    ) -> "ClinGenIndex":
        by_id: Dict[str, List[Dict[str, Any]]] = {}
        by_key: Dict[str, List[Dict[str, Any]]] = {}
        key_route: Dict[str, str] = {}
        by_allele_id: Dict[str, List[Dict[str, Any]]] = {}
        by_transcript_key: Dict[str, List[Dict[str, Any]]] = {}
        skipped = 0
        for case in cases:
            cid = _norm_id((case.get("provenance") or {}).get("clinvar_id"))
            if is_valid_variation_id(cid):
                by_id.setdefault(cid, []).append(case)
            else:
                skipped += 1
            key, route = cls._record_key_route(case, reference)
            if key is not None:
                by_key.setdefault(key, []).append(case)
                # A key reachable more than one way keeps its STRONGEST route, so a
                # weaker tier never relabels (let alone overrides) a stronger match.
                if key not in key_route or ROUTE_PRIORITY[route] < ROUTE_PRIORITY[key_route[key]]:
                    key_route[key] = route
            aid = allele_id_of(case)
            if aid is not None:
                by_allele_id.setdefault(aid, []).append(case)
            tkey = transcript_key_of(case)
            if tkey is not None:
                by_transcript_key.setdefault(tkey, []).append(case)
        return cls(by_id, skipped_invalid_id=skipped,
                   by_canonical_key=by_key, key_route=key_route,
                   by_allele_id=by_allele_id, by_transcript_key=by_transcript_key)

    @classmethod
    def from_fixture(
        cls, path: str, reference: Optional[ReferenceProvider] = None
    ) -> "ClinGenIndex":
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return cls.from_cases(data.get("cases", []), reference=reference)

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

    def route_for_key(self, provider_key: Optional[str]) -> Optional[str]:
        """The fallback route a canonical provider key was indexed under, or None."""
        if not provider_key:
            return None
        return self._key_route.get(provider_key)

    @property
    def allele_ids(self) -> set:
        """ClinVar Allele IDs present in the secondary allele-ID index (job1 task 3)."""
        return set(self._by_allele_id)

    @property
    def transcript_keys(self) -> set:
        """MANE-transcript identity keys present in the transcript index (job1 task 3)."""
        return set(self._by_transcript_key)

    def candidates_by_allele_id(self, allele_id: Optional[str]) -> List[Dict[str, Any]]:
        """All ClinGen records for a ClinVar Allele ID (possibly empty)."""
        if not allele_id:
            return []
        return list(self._by_allele_id.get(_norm_id(allele_id), []))

    def candidates_by_transcript_key(self, transcript_key: Optional[str]) -> List[Dict[str, Any]]:
        """All ClinGen records for a MANE-transcript identity key (possibly empty)."""
        if not transcript_key:
            return []
        return list(self._by_transcript_key.get(transcript_key, []))

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
        # Build the canonical index WITH the reference so genomic-HGVS indel tokens
        # (the `hgvs_g` tier) resolve to left-aligned keys that match the ClinVar side.
        return cls(ClinGenIndex.from_fixture(path, reference=reference), reference=reference)

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
        """Resolve evidence and carry the case's transcript identity (job1 task 4).

        The match logic lives in :meth:`_fetch_core`; this wrapper attaches the
        ClinVar case's MANE Select / RefSeq transcript identity to the returned bundle
        so a downstream consumer can name the transcript the evidence was interpreted
        against. Surfacing it (API -- Job 3; reports -- Jobs 2/3) is out of scope here.
        """
        bundle = self._fetch_core(case_or_variant)
        if bundle.transcript is None:
            bundle.transcript = TranscriptIdentity.from_case(case_or_variant)
        return bundle

    def _fetch_core(self, case_or_variant: Any) -> EvidenceBundle:
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
                "route": "variation_id",
                "ambiguous": False,
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

        # 2) Allele-ID fallback (job1 task 3): a ClinVar Allele ID is an allele-precise
        #    identity, stronger than a coordinate key, so it is tried before the
        #    canonical/SPDI/transcript routes.
        allele = self._allele_id_fallback(case_or_variant, expected_tier, provider_versions)
        if allele is not None:
            return allele

        # 3) Canonical-key fallback (coordinate locus, or a locus recovered from an
        #    NCBI SPDI -- job1 task 3). Returns a bundle (match, or a *blocking*
        #    normalization failure) when the case yields a locus; None to fall through.
        fallback = self._canonical_fallback(
            case_or_variant, expected_tier, provider_versions
        )
        if fallback is not None:
            return fallback

        # 4) MANE-transcript + coding-HGVS fallback (job1 task 3), the weakest tier.
        transcript = self._transcript_fallback(case_or_variant, expected_tier, provider_versions)
        if transcript is not None:
            return transcript

        # 5) True no-match. Preserve the historical warnings exactly.
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
                   "route": "none", "ambiguous": False, "variation_id": variation_id},
        )

    def _locus_for_case(self, case_or_variant: Any) -> tuple:
        """Best ``(locus, identity_route)`` for a case: locus block, else SPDI (task 3).

        A native ``locus`` block keys directly (``identity_route`` None -> the route is
        the ClinGen-side canonical route). When there is no locus block but the case
        carries an NCBI SPDI, it is resolved against the reference and the route is
        tagged ``spdi``. Returns ``(None, None)`` when neither is available.
        """
        loc = locus_from_case(case_or_variant)
        if loc is not None:
            return loc, None
        spdi = spdi_of(case_or_variant)
        if spdi:
            loc = parse_spdi(spdi, self.reference)
            if loc is not None:
                return loc, "spdi"
        return None, None

    def _candidate_bundle(
        self,
        cands: List[Dict[str, Any]],
        *,
        route: str,
        match_type: str,
        variant_key: Optional[str],
        expected_tier: Optional[str],
        provider_versions: Dict[str, str],
        base_match: Dict[str, Any],
    ) -> EvidenceBundle:
        """Resolve a non-coordinate candidate set into a bundle, with ambiguity rule.

        Shared by the allele-ID and transcript fallback tiers (job1 task 3). A key that
        maps to MULTIPLE, non-criteria-equivalent records is ambiguous -- nothing is
        imported, ``ambiguous_fallback_match`` is warned, and the candidate detail is
        preserved (the no-match is never treated as benign).
        """
        candidate_ids = sorted(_norm_id(c.get("id")) for c in cands)
        match = {
            "clingen_variation_id_match": False,
            "match_type": match_type,
            "route": route,
            "clinvar_expected": expected_tier,
            "candidate_count": len(cands),
            "candidate_ids": candidate_ids,
            **base_match,
        }
        if len(cands) > 1 and not records_criteria_equivalent(cands):
            match["ambiguous"] = True
            return EvidenceBundle(
                variant_key=variant_key, events=[], provider_versions=provider_versions,
                source_records=[dict(c) for c in cands],
                warnings=["ambiguous_fallback_match"], match=match,
            )
        chosen = min(cands, key=lambda r: self.index._sort_key(r, expected_tier))
        criteria = applied_criteria(chosen)
        events = derive_criteria_from_signals({"criteria": criteria})
        warnings: List[str] = []
        if len(cands) > 1:
            warnings.append("multiple_clingen_matches")
        if expected_tier is not None and chosen.get("expected") != expected_tier:
            warnings.append("label_disagreement")
        if not criteria:
            warnings.append("missing_criteria")
        match.update({"ambiguous": False, "clingen_case_id": chosen.get("id"),
                      "clingen_expected": chosen.get("expected")})
        return EvidenceBundle(
            variant_key=variant_key, events=events, provider_versions=provider_versions,
            source_records=[dict(c) for c in cands], warnings=warnings, match=match,
        )

    def _allele_id_fallback(
        self,
        case_or_variant: Any,
        expected_tier: Optional[str],
        provider_versions: Dict[str, str],
    ) -> Optional[EvidenceBundle]:
        """ClinVar Allele ID join (job1 task 3). None => no allele ID / no candidate."""
        allele_id = allele_id_of(case_or_variant)
        if not allele_id:
            return None
        cands = self.index.candidates_by_allele_id(allele_id)
        if not cands:
            return None
        return self._candidate_bundle(
            cands, route="clinvar_allele_id", match_type="clinvar_allele_id",
            variant_key="clinvar_allele_id:" + allele_id, expected_tier=expected_tier,
            provider_versions=provider_versions, base_match={"allele_id": allele_id},
        )

    def _transcript_fallback(
        self,
        case_or_variant: Any,
        expected_tier: Optional[str],
        provider_versions: Dict[str, str],
    ) -> Optional[EvidenceBundle]:
        """MANE-transcript + coding-HGVS join (job1 task 3). None => no key / candidate."""
        tkey = transcript_key_of(case_or_variant)
        if not tkey:
            return None
        cands = self.index.candidates_by_transcript_key(tkey)
        if not cands:
            return None
        return self._candidate_bundle(
            cands, route="hgvs_c_mane", match_type="transcript_hgvs",
            variant_key="transcript:" + tkey, expected_tier=expected_tier,
            provider_versions=provider_versions, base_match={"transcript_key": tkey},
        )

    def _canonical_fallback(
        self,
        case_or_variant: Any,
        expected_tier: Optional[str],
        provider_versions: Dict[str, str],
    ) -> Optional[EvidenceBundle]:
        """Attempt a canonical-key join. None => no locus / no canonical candidate.

        The locus comes from a native ``locus`` block or (job1 task 3) from an NCBI
        SPDI resolved against the reference. A locus that fails to normalize (blocking)
        returns a bundle whose match records ``normalization_failed`` rather than
        letting the join silently read as a clean non-match (acceptance criterion A).
        """
        loc, identity_route = self._locus_for_case(case_or_variant)
        if loc is None:
            return None  # bare id / no coordinates / unresolvable SPDI -> nothing to canonicalize.

        nr = normalize_locus(*loc, reference=self.reference)
        if not nr.ok or nr.blocking:
            # Never silently treat a failed normalization as a valid non-match.
            return EvidenceBundle(
                variant_key=None,
                events=[],
                provider_versions=provider_versions,
                source_records=[],
                warnings=list(nr.warnings) or ["normalization_failed"],
                match={"clingen_variation_id_match": False, "match_type": "none",
                       "route": "none", "ambiguous": False,
                       "normalized": False, "warnings": list(nr.warnings)},
            )

        cands = self.index.candidates_by_key(nr.provider_key)
        if not cands:
            return None  # normalized fine, but ClinGen has no record at this key.

        # Which fallback tier produced this key. When the ClinVar-side locus came from
        # an SPDI the reported route is ``spdi``; otherwise it is the ClinGen-side
        # canonical route (canonical_snv / reference_backed_indel / hgvs_g). A ClinVar
        # SNV can only collide with a ClinGen SNV key, so the ClinGen-side route
        # already reflects the join's strength.
        route = identity_route or self.index.route_for_key(nr.provider_key) or (
            "canonical_snv" if not nr.is_indel else "reference_backed_indel"
        )
        candidate_ids = sorted(_norm_id(c.get("id")) for c in cands)
        base_match = {
            "clingen_variation_id_match": False,
            "match_type": "canonical_key",
            "route": route,
            "canonical_key": nr.key,
            "provider_key": nr.provider_key,
            "normalization_method": nr.method,
            "clinvar_expected": expected_tier,
            "candidate_count": len(cands),
            "candidate_ids": candidate_ids,
        }

        # Ambiguity rule (job1): a fallback key with MULTIPLE, non-equivalent ClinGen
        # records is ambiguous -- enrich ONLY when every candidate imports the identical
        # criteria. Otherwise import nothing, warn deterministically, and preserve the
        # candidate detail for debugging. Missing evidence stays unknown, never guessed.
        if len(cands) > 1 and not records_criteria_equivalent(cands):
            warnings = ["ambiguous_fallback_match"]
            warnings.extend(w for w in nr.warnings if w not in warnings)
            base_match["ambiguous"] = True
            return EvidenceBundle(
                variant_key=nr.key,
                events=[],
                provider_versions=provider_versions,
                source_records=[dict(c) for c in cands],
                warnings=warnings,
                match=base_match,
            )

        chosen = self.index.resolve_by_key(nr.provider_key, expected_tier)
        criteria = applied_criteria(chosen)
        events = derive_criteria_from_signals({"criteria": criteria})

        warnings = []
        if len(cands) > 1:
            # Multiple but criteria-equivalent -> safe to enrich; still flagged.
            warnings.append("multiple_clingen_matches")
        if expected_tier is not None and chosen.get("expected") != expected_tier:
            warnings.append("label_disagreement")
        if not criteria:
            warnings.append("missing_criteria")
        # Carry the advisory indel flag through so a non-canonical indel key is visible.
        warnings.extend(w for w in nr.warnings if w not in warnings)

        base_match.update({
            "ambiguous": False,
            "clingen_case_id": chosen.get("id"),
            "clingen_expected": chosen.get("expected"),
        })
        return EvidenceBundle(
            variant_key=nr.key,
            events=events,
            provider_versions=provider_versions,
            source_records=[dict(c) for c in cands],
            warnings=warnings,
            match=base_match,
        )
