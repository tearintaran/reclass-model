"""Variant normalization (spec 03 / memo S5).

Pure and deterministic. The reference-free operations need NO genome:

  * multiallelic decomposition  (one ALT per record), and
  * parsimonious trimming        (remove shared suffix then shared prefix bases,
                                  adjusting POS, keeping >= 1 base on each side).

Indel **left-alignment** genuinely requires the reference sequence. Per acceptance
criterion AC-3.4, `left_align` stays a LOUD hook (raises) when called WITHOUT a
reference — so repeat-shifted indels are never silently mis-normalized. When a
`ReferenceProvider` is supplied, `left_align` performs reference-anchored
left-shifting + parsimony (the vt/bcftools algorithm), yielding the canonical
leftmost representation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .reference import ReferenceLookupError, ReferenceProvider

_VALID_ALLELE = re.compile(r"^[ACGTN]+$")

#: The only genome build this engine normalizes against today.
DEFAULT_BUILD = "GRCh38"

#: Build tokens recognised at the front of a canonical key. Used to disambiguate a
#: 5-field storage key (``GRCh38-1-100-A-G``) from a 4-field provider key
#: (``1-100-A-G``); allele/pos/chrom fields never contain a build token.
KNOWN_BUILDS = {"grch38", "grch37", "hg38", "hg19", "ncbi36", "hg18"}


def normalize_chrom(chrom: Any) -> str:
    """Normalize a contig name to the bare convention used across this project.

    Storage, the provider keys, the REVEL index, and the ClinVar VCF all use bare
    contig names (``1``, ``X``, ``MT``). This strips a leading ``chr``/``CHR`` so
    ``chr1`` and ``1`` resolve to the same canonical key; everything else is kept
    verbatim (case preserved for ``X``/``Y``/``MT``).
    """
    s = str(chrom).strip()
    if s[:3].lower() == "chr":
        s = s[3:]
    return s


@dataclass(frozen=True)
class Variant:
    chrom: str
    pos: int
    ref: str
    alt: str

    def key(self) -> tuple:
        return (self.chrom, self.pos, self.ref, self.alt)

    def provider_key(self) -> str:
        """Build-stripped provider key, e.g. ``1-100-A-G``."""
        return provider_key(self.chrom, self.pos, self.ref, self.alt)

    def canonical_key(self, build: str = DEFAULT_BUILD) -> str:
        """Storage-compatible canonical key, e.g. ``GRCh38-1-100-A-G``."""
        return canonical_key(self.chrom, self.pos, self.ref, self.alt, build)

    def __str__(self) -> str:
        return f"{self.chrom}-{self.pos}-{self.ref}-{self.alt}"


# --------------------------------------------------------------------------- #
# Canonical variant identity (roadmap §1, tasks 1-2)                           #
# --------------------------------------------------------------------------- #
# One key format spans fixtures, evidence providers, storage, and the API:
#
#   provider key   1-100-A-G            (build-stripped; assumes the default build)
#   canonical key  GRCh38-1-100-A-G     (storage form: build token + provider key)
#
# The canonical key is the provider key with an explicit build token prepended;
# the provider key is the canonical key with its leading ``<build>-`` removed. The
# canonical form is byte-compatible with ``storage.classifications.variant_key``
# (treated here as a fixed contract), and the provider form is byte-compatible with
# the existing ``evidence.revel.variant_key`` / gnomAD variant ids. These helpers are
# the single, shared place that maps between the two so no source grows its own key.


def provider_key(chrom: Any, pos: Any, ref: str, alt: str) -> str:
    """Canonical build-stripped provider key ``chrom-pos-ref-alt`` (e.g. ``1-100-A-G``)."""
    return f"{normalize_chrom(chrom)}-{int(pos)}-{str(ref).upper()}-{str(alt).upper()}"


def canonical_key(chrom: Any, pos: Any, ref: str, alt: str, build: str = DEFAULT_BUILD) -> str:
    """Storage-compatible canonical key ``build-chrom-pos-ref-alt`` (``GRCh38-1-100-A-G``)."""
    return f"{build}-{provider_key(chrom, pos, ref, alt)}"


def add_build(provider_key_str: str, build: str = DEFAULT_BUILD) -> str:
    """Promote a provider key to a storage-form canonical key by prefixing the build."""
    return f"{build}-{provider_key_str}"


def parse_key(key: str) -> Dict[str, Any]:
    """Parse a provider or canonical key into its components.

    Returns ``{build, chrom, pos, ref, alt}``; ``build`` is ``None`` for a bare
    provider key. Raises ``ValueError`` for a key that is neither 4- nor 5-field.
    Alleles/pos/chrom never contain ``-``, so splitting is unambiguous; a 5-field
    key is treated as build-prefixed only when its first field is a known build.
    """
    parts = str(key).split("-")
    if len(parts) == 5 and parts[0].lower() in KNOWN_BUILDS:
        build, chrom, pos, ref, alt = parts
    elif len(parts) == 4:
        build, (chrom, pos, ref, alt) = None, parts
    else:
        raise ValueError(f"not a variant key: {key!r}")
    return {"build": build, "chrom": chrom, "pos": int(pos), "ref": ref, "alt": alt}


def strip_build(key: str) -> Tuple[Optional[str], str]:
    """Split a canonical key into ``(build, provider_key)``.

    A bare provider key returns ``(None, key)`` unchanged, so this is safe to call
    on either form.
    """
    p = parse_key(key)
    return p["build"], provider_key(p["chrom"], p["pos"], p["ref"], p["alt"])


def provider_key_of(key: str) -> str:
    """Return the build-stripped provider key for any provider/canonical key."""
    return strip_build(key)[1]


# --------------------------------------------------------------------------- #
# Transcript identity normalization (job1 task 3: MANE/HGVS transcript route)  #
# --------------------------------------------------------------------------- #
def normalize_transcript(transcript: Any) -> Optional[str]:
    """Version-stripped, upper-cased transcript accession, or None.

    ``NM_000277.3`` and ``nm_000277.4`` both normalize to ``NM_000277`` so two
    sources that name the same MANE Select transcript at different *versions* still
    join. The version is intentionally dropped from the match key (a c.HGVS on a
    transcript means the same edit regardless of the transcript's minor version);
    keep the full versioned accession alongside when version-exact identity matters.
    """
    if transcript is None:
        return None
    s = str(transcript).strip()
    if not s:
        return None
    return s.split(".")[0].upper()


def transcript_hgvs_key(transcript: Any, hgvs_c: Any) -> Optional[str]:
    """Identity key for a (MANE) transcript + coding HGVS, version-stripped.

    ``('NM_000277.3', 'c.1A>G')`` -> ``'NM_000277:c.1A>G'``. Returns None when either
    component is missing, so a record with no usable transcript identity simply does
    not key (an honest miss, never a guess). Internal whitespace in the coding HGVS is
    collapsed so cosmetically different spellings of the same edit still join.
    """
    t = normalize_transcript(transcript)
    if not t or hgvs_c is None:
        return None
    c = "".join(str(hgvs_c).split())
    if not c:
        return None
    return f"{t}:{c}"


def _validate_allele(allele: str, kind: str) -> None:
    if not allele or not _VALID_ALLELE.match(allele.upper()):
        raise ValueError(f"invalid {kind} allele: {allele!r} (expected non-empty A/C/G/T/N)")


def split_multiallelic(variant: Variant) -> List[Variant]:
    """Decompose a comma-separated ALT into one biallelic Variant per ALT."""
    _validate_allele(variant.ref, "ref")
    alts = variant.alt.split(",")
    out: List[Variant] = []
    for a in alts:
        _validate_allele(a, "alt")
        out.append(Variant(variant.chrom, variant.pos, variant.ref.upper(), a.upper()))
    return out


def trim(variant: Variant) -> Variant:
    """Parsimoniously trim a biallelic variant.

    Removes the shared suffix first, then the shared prefix (advancing POS),
    always leaving at least one base in both REF and ALT.
    """
    if "," in variant.alt:
        raise ValueError("trim() requires a biallelic variant; call split_multiallelic first")
    _validate_allele(variant.ref, "ref")
    _validate_allele(variant.alt, "alt")

    chrom, pos = variant.chrom, variant.pos
    ref, alt = variant.ref.upper(), variant.alt.upper()

    while len(ref) > 1 and len(alt) > 1 and ref[-1] == alt[-1]:
        ref, alt = ref[:-1], alt[:-1]

    while len(ref) > 1 and len(alt) > 1 and ref[0] == alt[0]:
        ref, alt = ref[1:], alt[1:]
        pos += 1

    return Variant(chrom, pos, ref, alt)


def normalize(variant: Variant) -> List[Variant]:
    """Full reference-free normalization: split then parsimoniously trim each ALT."""
    return [trim(v) for v in split_multiallelic(variant)]


def variant_key(variant: Variant, build: str = DEFAULT_BUILD) -> str:
    """Canonical string key for a (normalized) variant, e.g. ``GRCh38-1-100-A-G``.

    This is the storage-compatible canonical key (see :func:`canonical_key`). It is
    the shared identity across fixtures, evidence providers, storage, and the future
    API; :func:`provider_key_of` derives the build-stripped provider form when a
    source (REVEL, gnomAD) keys without a build token.
    """
    return canonical_key(variant.chrom, variant.pos, variant.ref, variant.alt, build)


def left_align(variant: Variant, reference: Optional[ReferenceProvider] = None) -> Variant:
    """Reference-anchored left-alignment + parsimony for a biallelic variant.

    Without a `reference` this remains the LOUD hook (AC-3.4) and raises
    `NotImplementedError`: repeat-shifted indels must not be silently normalized.

    With a `reference`, shifts the variant as far left as the reference allows while
    preserving the represented sequence, then left-trims for parsimony, returning the
    canonical leftmost `Variant`. SNVs/MNVs are returned unchanged (after parsimony).
    """
    if reference is None:
        raise NotImplementedError(
            "left_align requires a GRCh38 reference provider (AC-3.4). Without a "
            "reference, repeat-shifted indels must NOT be silently normalized. Pass a "
            "ReferenceProvider to enable reference-anchored left-shifting."
        )

    if "," in variant.alt:
        raise ValueError("left_align requires a biallelic variant; call split_multiallelic first")
    _validate_allele(variant.ref, "ref")
    _validate_allele(variant.alt, "alt")

    chrom = variant.chrom
    pos = variant.pos
    ref = variant.ref.upper()
    alt = variant.alt.upper()

    if ref == alt:
        raise ValueError(f"not a variant: ref == alt ({ref!r}) at {chrom}:{pos}")

    # Verify the asserted REF matches the reference (tolerate N in the reference).
    asserted = reference.sequence(chrom, pos, pos + len(ref) - 1)
    if "N" not in asserted and asserted != ref:
        raise ValueError(
            f"REF mismatch at {chrom}:{pos}: variant says {ref!r} but reference has "
            f"{asserted!r}"
        )

    # Right-trim shared trailing bases; whenever an allele empties, extend one base
    # to the left from the reference (this is what shifts repeats leftward).
    while True:
        if ref and alt and ref[-1] == alt[-1]:
            ref, alt = ref[:-1], alt[:-1]
            if not ref or not alt:
                if pos <= 1:
                    raise ReferenceLookupError(
                        f"cannot left-align past contig start at {chrom}:{pos}"
                    )
                base = reference.base_at(chrom, pos - 1)
                ref, alt = base + ref, base + alt
                pos -= 1
            continue
        if not ref or not alt:
            if pos <= 1:
                raise ReferenceLookupError(
                    f"cannot left-align past contig start at {chrom}:{pos}"
                )
            base = reference.base_at(chrom, pos - 1)
            ref, alt = base + ref, base + alt
            pos -= 1
            continue
        break

    # Left-trim shared leading bases for parsimony, keeping >= 1 base on each side.
    while len(ref) > 1 and len(alt) > 1 and ref[0] == alt[0]:
        ref, alt = ref[1:], alt[1:]
        pos += 1

    return Variant(chrom, pos, ref, alt)


def normalize_with_reference(variant: Variant, reference: ReferenceProvider) -> List[Variant]:
    """Full normalization with a reference: split, trim, then left-align each ALT."""
    return [left_align(trim(v), reference) for v in split_multiallelic(variant)]


# --------------------------------------------------------------------------- #
# Reference-aware normalization workflow (roadmap §1, tasks 3, 8)             #
# --------------------------------------------------------------------------- #
# Warnings a normalization can raise. BLOCKING warnings mean the result must NOT be
# treated as a valid (canonical) identity: a join that hits one must surface it, not
# silently record a non-match (acceptance criterion A: "No automated join silently
# treats a failed normalization as a valid non-match"). ADVISORY warnings mean a
# best-effort key was produced but is not guaranteed canonical.
BLOCKING_WARNINGS = {
    "invalid_allele",
    "missing_locus",
    "multiallelic_not_decomposed",
    "normalization_failed",
    "reference_lookup_failed",
    "reference_mismatch",
}
#: An indel normalized reference-free (no FASTA available): parsimony-trimmed but not
#: left-aligned, so repeat-shifted spellings may not collapse. Flagged, never silent.
ADVISORY_WARNINGS = {"indel_not_left_aligned"}


@dataclass(frozen=True)
class NormalizationResult:
    """Outcome of normalizing one biallelic locus into a canonical identity.

    ``variant`` is the normalized variant (``None`` if normalization failed);
    ``key`` is its storage-form canonical key; ``provider_key`` the build-stripped
    form; ``method`` records how it was normalized; ``warnings`` carries any
    blocking/advisory flags so a caller never treats a failed normalization as a
    clean result.
    """

    variant: Optional[Variant]
    key: Optional[str]
    provider_key: Optional[str]
    method: str
    is_indel: bool
    warnings: Tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        """True when a normalized variant (and key) was produced."""
        return self.variant is not None

    @property
    def blocking(self) -> bool:
        """True when any warning means the identity must not be trusted as canonical."""
        return any(w in BLOCKING_WARNINGS for w in self.warnings)


def _failed_result(method: str, is_indel: bool, warnings: List[str]) -> NormalizationResult:
    return NormalizationResult(
        variant=None, key=None, provider_key=None,
        method=method, is_indel=is_indel, warnings=tuple(warnings),
    )


def normalize_locus(
    chrom: Any,
    pos: Any,
    ref: str,
    alt: str,
    reference: Optional[ReferenceProvider] = None,
    build: str = DEFAULT_BUILD,
) -> NormalizationResult:
    """Normalize one biallelic locus into a canonical identity. Never raises.

    SNVs/MNVs need no reference (parsimony trim only). Indels are reference-anchored
    left-aligned when a ``reference`` is supplied; without one they are trimmed only
    and flagged ``indel_not_left_aligned`` (advisory) because repeat-shifted spellings
    may not collapse. A reference REF mismatch or out-of-range lookup yields a
    blocking warning and ``variant=None`` -- the loud-failure contract from
    ``left_align`` (AC-3.4) carried through into the join workflow.
    """
    try:
        _validate_allele(ref, "ref")
    except ValueError:
        return _failed_result("failed", False, ["invalid_allele", "normalization_failed"])
    if "," in str(alt):
        return _failed_result("failed", False, ["multiallelic_not_decomposed", "normalization_failed"])
    try:
        _validate_allele(alt, "alt")
    except ValueError:
        return _failed_result("failed", False, ["invalid_allele", "normalization_failed"])

    trimmed = trim(Variant(normalize_chrom(chrom), int(pos), str(ref).upper(), str(alt).upper()))
    is_indel = len(trimmed.ref) != len(trimmed.alt)

    if not is_indel:
        method = "snv" if len(trimmed.ref) == 1 else "mnv"
        return NormalizationResult(
            variant=trimmed, key=variant_key(trimmed, build),
            provider_key=trimmed.provider_key(), method=method,
            is_indel=False, warnings=(),
        )

    # Indel.
    if reference is None:
        return NormalizationResult(
            variant=trimmed, key=variant_key(trimmed, build),
            provider_key=trimmed.provider_key(), method="reference_free_trim",
            is_indel=True, warnings=("indel_not_left_aligned",),
        )

    try:
        aligned = left_align(trimmed, reference)
    except ReferenceLookupError:
        return _failed_result("failed", True, ["reference_lookup_failed"])
    except ValueError:
        # REF disagrees with the reference (or not-a-variant): never guess.
        return _failed_result("failed", True, ["reference_mismatch", "normalization_failed"])
    return NormalizationResult(
        variant=aligned, key=variant_key(aligned, build),
        provider_key=aligned.provider_key(), method="reference_left_aligned",
        is_indel=True, warnings=(),
    )


def locus_from_case(case: Any) -> Optional[Tuple[str, int, str, str]]:
    """Pull ``(chrom, pos, ref, alt)`` from a fixture case ``locus`` block, or None."""
    if not isinstance(case, dict):
        return None
    loc = case.get("locus", case)
    try:
        return (str(loc["chrom"]), int(loc["pos"]), str(loc["ref"]), str(loc["alt"]))
    except (KeyError, TypeError, ValueError):
        return None


def normalize_case(
    case: Any,
    reference: Optional[ReferenceProvider] = None,
    build: str = DEFAULT_BUILD,
) -> NormalizationResult:
    """Normalize a fixture case's locus into a canonical identity (never raises)."""
    loc = locus_from_case(case)
    if loc is None:
        return _failed_result("failed", False, ["missing_locus"])
    return normalize_locus(*loc, reference=reference, build=build)


# --------------------------------------------------------------------------- #
# Identity audit: SNV/indel duplicate & mismatch rates (roadmap §1, task 7)   #
# --------------------------------------------------------------------------- #
def _coerce_locus(item: Any) -> Optional[Tuple[str, int, str, str]]:
    if isinstance(item, dict):
        return locus_from_case(item)
    if isinstance(item, (tuple, list)) and len(item) == 4:
        try:
            return (str(item[0]), int(item[1]), str(item[2]), str(item[3]))
        except (TypeError, ValueError):
            return None
    if isinstance(item, str):
        try:
            p = parse_key(item)
            return (p["chrom"], p["pos"], p["ref"], p["alt"])
        except ValueError:
            return None
    return None


def _dup_stats(keys: List[str]) -> Dict[str, int]:
    """Distinct-key and collision counts for a list of (non-None) keys."""
    seen: Dict[str, int] = {}
    for k in keys:
        seen[k] = seen.get(k, 0) + 1
    distinct = len(seen)
    collision_keys = sum(1 for n in seen.values() if n > 1)
    # Number of loci that share a key with at least one other locus.
    duplicated_loci = sum(n for n in seen.values() if n > 1)
    return {
        "loci": len(keys),
        "distinct_keys": distinct,
        "collision_keys": collision_keys,
        "duplicated_loci": duplicated_loci,
    }


def audit_loci(
    loci: Iterable[Any],
    reference: Optional[ReferenceProvider] = None,
    build: str = DEFAULT_BUILD,
) -> Dict[str, Any]:
    """Measure SNV/indel duplicate and mismatch rates before vs after normalization.

    ``loci`` is any iterable of ``(chrom,pos,ref,alt)`` tuples, fixture case dicts,
    or key strings. The *before* view uses reference-free parsimony keys; the *after*
    view uses reference-anchored left-aligned keys for indels (SNV keys are identical
    in both). Without a ``reference`` the *after* view is reported as unavailable and
    indels are counted as not-left-aligned, so the gap is explicit, never hidden.
    """
    total = invalid = 0
    snv_free: List[str] = []
    indel_free: List[str] = []
    snv_aligned: List[str] = []
    indel_aligned: List[str] = []
    indel_unaligned = 0          # indels we could not left-align (no reference)
    reference_mismatch = 0       # REF disagreed with the reference
    reference_lookup_failed = 0  # out-of-range / unknown contig

    for item in loci:
        loc = _coerce_locus(item)
        if loc is None:
            invalid += 1
            continue
        total += 1
        # Before: reference-free.
        free = normalize_locus(*loc, reference=None, build=build)
        if not free.ok:
            invalid += 1
            total -= 1
            continue
        if free.is_indel:
            indel_free.append(free.key)
        else:
            snv_free.append(free.key)

        # After: reference-backed (only meaningful for indels).
        if not free.is_indel:
            snv_aligned.append(free.key)
            continue
        if reference is None:
            indel_unaligned += 1
            continue
        backed = normalize_locus(*loc, reference=reference, build=build)
        if backed.ok:
            indel_aligned.append(backed.key)
        elif "reference_mismatch" in backed.warnings:
            reference_mismatch += 1
        elif "reference_lookup_failed" in backed.warnings:
            reference_lookup_failed += 1

    out: Dict[str, Any] = {
        "total_loci": total,
        "invalid_loci": invalid,
        "snv": len(snv_free),
        "indel": len(indel_free),
        "reference_available": reference is not None,
        "reference_free": {
            "snv": _dup_stats(snv_free),
            "indel": _dup_stats(indel_free),
        },
    }
    if reference is not None:
        out["reference_backed"] = {
            "snv": _dup_stats(snv_aligned),
            "indel": _dup_stats(indel_aligned),
            "reference_mismatch": reference_mismatch,
            "reference_lookup_failed": reference_lookup_failed,
        }
        # How many extra indel collisions reference-backed normalization revealed.
        before = out["reference_free"]["indel"]["duplicated_loci"]
        after = out["reference_backed"]["indel"]["duplicated_loci"]
        out["reference_backed"]["indel_duplicates_revealed"] = after - before
    else:
        out["reference_backed"] = None
        out["indel_not_left_aligned"] = indel_unaligned
    return out
