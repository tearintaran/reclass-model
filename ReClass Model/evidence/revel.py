"""REVEL computational-evidence provider, keyed by genomic locus.

REVEL ships a precomputed pathogenicity score for every possible human missense
variant (~80M rows, ~7GB uncompressed). The one-off `ingest/enrich_revel.py` script
streamed that CSV straight out of the zip and wrote the matching scores back into
the ClinVar benchmark. This module turns that logic into a reusable, cached,
provenance-rich :class:`~evidence.providers.EvidenceProvider`:

  * :class:`RevelIndex`    -- ``(chrom, pos, ref, alt) -> REVEL score`` lookup, built
                              once by streaming the REVEL zip for a set of target
                              loci, then persisted to a small local cache so repeated
                              dev runs are offline after the first build,
  * :class:`RevelProvider` -- resolves a REVEL score for a case/variant and returns
                              the same PP3/BP4 :class:`EvidenceEvent`s the scoring
                              engine derives from ``signals.revel`` (so a score routed
                              through this provider reproduces the legacy fixture
                              behavior byte-for-byte).

`fetch` is pure given a fixed index/cache snapshot and never raises on "no score":
an absent score yields an empty-but-valid bundle with a deterministic warning.
"""

from __future__ import annotations

import json
import os
import zipfile
from dataclasses import asdict, dataclass
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from engine.scoring import derive_criteria_from_signals
from engine.normalize import canonical_key as _canonical_key
from engine.normalize import provider_key as _provider_key

from .model import EvidenceBundle
from .providers import EvidenceProvider

#: Stable provider identity / source version.
PROVIDER_NAME = "revel"
PROVIDER_VERSION = "REVEL_v1.3"

#: Where the local lookup cache lives (relative to ``ReClass Model/``).
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
DEFAULT_CACHE_PATH = os.path.join(_ROOT, "data", "cache", "providers", "revel_cache.json")


def variant_key(chrom: Any, pos: Any, ref: Any, alt: Any) -> str:
    """Canonical build-stripped provider key ``"chrom-pos-ref-alt"`` (``1-12345-C-A``).

    Delegates to :func:`engine.normalize.provider_key` so REVEL, gnomAD, storage, and
    the API all share one identity (the provider key is the storage canonical
    key minus its build token).
    """
    return _provider_key(chrom, pos, ref, alt)


def locus_of(case_or_variant: Any) -> Optional[Tuple[str, int, str, str]]:
    """Pull ``(chrom, pos, ref, alt)`` from a case/variant, or None if unresolvable.

    Accepts a full fixture case dict (with a ``locus`` block), a bare
    ``{chrom,pos,ref,alt}`` dict, a ``(chrom, pos, ref, alt)`` tuple/list, or a
    ``"chrom-pos-ref-alt"`` string.
    """
    src: Any = case_or_variant
    if isinstance(src, dict):
        src = src.get("locus", src)
        try:
            return (str(src["chrom"]), int(src["pos"]), src["ref"], src["alt"])
        except (KeyError, TypeError, ValueError):
            return None
    if isinstance(src, (tuple, list)) and len(src) == 4:
        try:
            return (str(src[0]), int(src[1]), src[2], src[3])
        except (TypeError, ValueError):
            return None
    if isinstance(src, str):
        parts = src.split("-")
        if len(parts) == 4:
            try:
                return (parts[0], int(parts[1]), parts[2], parts[3])
            except ValueError:
                return None
    return None


@dataclass
class ProviderStats:
    """Per-run provider call statistics (gap.md section 1, task 6).

    ``queried`` total fetches; ``matched`` resolved a score; ``absent`` no score for
    the locus; ``failed`` the locus could not be parsed into a key; ``cached`` served
    from the local index (for REVEL every resolved lookup is cache-served).
    """

    queried: int = 0
    matched: int = 0
    absent: int = 0
    failed: int = 0
    cached: int = 0

    def as_dict(self) -> Dict[str, int]:
        return asdict(self)


class RevelIndex:
    """``(chrom, pos, ref, alt) -> REVEL score`` lookup backed by a small JSON cache."""

    def __init__(self, scores: Optional[Dict[str, float]] = None) -> None:
        self._scores: Dict[str, float] = dict(scores or {})

    # -- construction ------------------------------------------------------- #
    @classmethod
    def from_scores(cls, scores: Dict[str, float]) -> "RevelIndex":
        return cls(scores)

    @classmethod
    def from_cache(cls, path: str = DEFAULT_CACHE_PATH) -> "RevelIndex":
        """Load a previously built cache; an absent file yields an empty index."""
        if not os.path.exists(path):
            return cls({})
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return cls(dict(data.get("scores") or {}))

    @classmethod
    def build_from_zip(
        cls,
        zip_path: str,
        target_keys: Optional[Iterable[str]] = None,
        *,
        progress: Optional[Any] = None,
    ) -> "RevelIndex":
        """Stream the REVEL CSV out of ``zip_path``, keeping only ``target_keys``.

        We never extract the ~7GB CSV; we read it line-by-line from inside the zip
        and retain only loci of interest, taking the MAX score on a collision (the
        legacy script's tie-break). ``target_keys`` are canonical
        ``variant_key`` strings; pass None to retain everything (large!).
        """
        wanted: Optional[Set[str]] = set(target_keys) if target_keys is not None else None
        scores: Dict[str, float] = {}
        with zipfile.ZipFile(zip_path) as zf:
            member = _find_csv_member(zf)
            with zf.open(member, "r") as raw:
                header = raw.readline().decode("utf-8").strip().split(",")
                col = {name: idx for idx, name in enumerate(header)}
                c_chr, c_pos = col["chr"], col["grch38_pos"]
                c_ref, c_alt, c_score = col["ref"], col["alt"], col["REVEL"]
                for n, bline in enumerate(raw):
                    if progress is not None and n and n % 10_000_000 == 0:
                        progress(n, len(scores))
                    parts = bline.decode("utf-8").rstrip("\n").split(",")
                    gpos = parts[c_pos]
                    if gpos == "." or not gpos:
                        continue
                    key = f"{parts[c_chr]}-{int(gpos)}-{parts[c_ref]}-{parts[c_alt]}"
                    if wanted is not None and key not in wanted:
                        continue
                    try:
                        score = float(parts[c_score])
                    except ValueError:
                        continue
                    prev = scores.get(key)
                    if prev is None or score > prev:
                        scores[key] = score
        return cls(scores)

    # -- persistence -------------------------------------------------------- #
    def to_cache(self, path: str = DEFAULT_CACHE_PATH) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        payload = {
            "provider": PROVIDER_NAME,
            "version": PROVIDER_VERSION,
            "scores": dict(sorted(self._scores.items())),
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, sort_keys=True)
            f.write("\n")

    # -- inspection / lookup ------------------------------------------------ #
    def __len__(self) -> int:
        return len(self._scores)

    def __contains__(self, key: str) -> bool:
        return key in self._scores

    def lookup(self, chrom: Any, pos: Any, ref: Any, alt: Any) -> Optional[float]:
        return self._scores.get(variant_key(chrom, pos, ref, alt))


class RevelProvider(EvidenceProvider):
    """Resolve a REVEL score for a locus and emit the engine's PP3/BP4 events."""

    name = PROVIDER_NAME
    version = PROVIDER_VERSION

    def __init__(self, index: RevelIndex) -> None:
        self.index = index
        self.stats = ProviderStats()

    @classmethod
    def from_cache(cls, path: str = DEFAULT_CACHE_PATH) -> "RevelProvider":
        return cls(RevelIndex.from_cache(path))

    @classmethod
    def from_scores(cls, scores: Dict[str, float]) -> "RevelProvider":
        return cls(RevelIndex.from_scores(scores))

    def fetch(self, case_or_variant: Any) -> EvidenceBundle:
        self.stats.queried += 1
        provider_versions = {self.name: self.version}
        loc = locus_of(case_or_variant)

        if loc is None:
            self.stats.failed += 1
            return EvidenceBundle(
                variant_key=None,
                events=[],
                provider_versions=provider_versions,
                source_records=[],
                warnings=["missing_locus"],
                match={"revel_match": False, "variant_key": None},
            )

        chrom, pos, ref, alt = loc
        key = variant_key(chrom, pos, ref, alt)
        score = self.index.lookup(chrom, pos, ref, alt)

        if score is None:
            self.stats.absent += 1
            return EvidenceBundle(
                variant_key=key,
                events=[],
                provider_versions=provider_versions,
                source_records=[],
                warnings=["no_revel_score"],
                match={"revel_match": False, "match_type": "canonical_locus",
                       "variant_key": key, "canonical_key": _canonical_key(chrom, pos, ref, alt),
                       "revel_score": None},
            )

        # A resolved score is served from the local index (the REVEL cache).
        self.stats.matched += 1
        self.stats.cached += 1

        # Route the score through the SAME engine mapping the legacy fixture used, so
        # a score fetched here reproduces the historical events/classification exactly.
        events = derive_criteria_from_signals({"revel": score})
        warnings: List[str] = []
        if not events:
            # Valid score, but it sits in REVEL's indeterminate band -> no criterion.
            warnings.append("revel_indeterminate_band")

        source_records = [
            {
                "source": "REVEL",
                "dataset": self.version,
                "variant_key": key,
                "chrom": chrom,
                "grch38_pos": pos,
                "ref": ref,
                "alt": alt,
                "revel_score": score,
            }
        ]
        match = {
            "revel_match": True,
            "match_type": "canonical_locus",
            "variant_key": key,
            "canonical_key": _canonical_key(chrom, pos, ref, alt),
            "revel_score": score,
            "actionable": bool(events),
        }
        return EvidenceBundle(
            variant_key=key,
            events=events,
            provider_versions=provider_versions,
            source_records=source_records,
            warnings=warnings,
            match=match,
        )


def _find_csv_member(zf: zipfile.ZipFile) -> str:
    """REVEL v1.3 ships a single 'revel_with_transcript_ids' member (no extension);
    fall back to the largest entry so this survives a future repackaging."""
    for name in zf.namelist():
        if "revel" in name.lower():
            return name
    return max(zf.infolist(), key=lambda zi: zi.file_size).filename
