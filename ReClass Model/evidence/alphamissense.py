"""AlphaMissense computational-evidence provider, keyed by genomic locus.

AlphaMissense (Cheng et al. 2023) ships a precomputed pathogenicity score for every
possible human missense variant (~71M rows). Like REVEL, the full table is far too
large to commit, so this provider follows the exact REVEL design (gap.md A3, "Mirror
the REVEL provider + cache + threshold-bin design"):

  * :class:`AlphaMissenseIndex`    -- ``(chrom, pos, ref, alt) -> AlphaMissense score``
                                      lookup, built once by streaming the AlphaMissense
                                      TSV (gzip) for a set of target loci, then persisted
                                      to a small local cache so repeated dev runs are
                                      offline after the first build,
  * :class:`AlphaMissenseProvider` -- resolves a score for a case/variant and returns
                                      the PP3/BP4 :class:`EvidenceEvent`s the scoring
                                      engine derives from ``signals.alphamissense`` (so a
                                      score routed through this provider reproduces the
                                      engine's threshold bins byte-for-byte).

`fetch` is pure given a fixed index/cache snapshot and never raises on "no score":
an absent score yields an empty-but-valid bundle with a deterministic warning. The
threshold bins live in ``engine/configs/computational_ext_v1.json`` (not base_v1.json),
so the calibration is reviewable and adding the provider changes no governed config.
"""

from __future__ import annotations

import gzip
import json
import os
from typing import Any, Dict, Iterable, List, Optional, Set

from engine.normalize import canonical_key as _canonical_key
from engine.scoring import derive_criteria_from_signals

from . import cache_manifest
from .model import EvidenceBundle
from .providers import EvidenceProvider
from .revel import ProviderStats, locus_of, variant_key

#: Stable provider identity / source version.
PROVIDER_NAME = "alphamissense"
PROVIDER_VERSION = "AlphaMissense_v1"
SOURCE_LABEL = "AlphaMissense (Cheng et al. 2023, hg38 precomputed missense scores)"
SOURCE_URL = "https://alphamissense.hegelab.org/"

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
DEFAULT_CACHE_PATH = os.path.join(_ROOT, "data", "cache", "providers", "alphamissense_cache.json")


class AlphaMissenseIndex:
    """``(chrom, pos, ref, alt) -> AlphaMissense score`` lookup backed by a JSON cache."""

    def __init__(self, scores: Optional[Dict[str, float]] = None) -> None:
        self._scores: Dict[str, float] = dict(scores or {})

    # -- construction ------------------------------------------------------- #
    @classmethod
    def from_scores(cls, scores: Dict[str, float]) -> "AlphaMissenseIndex":
        return cls(scores)

    @classmethod
    def from_cache(cls, path: str = DEFAULT_CACHE_PATH) -> "AlphaMissenseIndex":
        """Load a previously built cache; an absent file yields an empty index."""
        if not os.path.exists(path):
            return cls({})
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return cls(dict(data.get("scores") or {}))

    @classmethod
    def build_from_tsv(
        cls,
        tsv_gz_path: str,
        target_keys: Optional[Iterable[str]] = None,
        *,
        progress: Optional[Any] = None,
    ) -> "AlphaMissenseIndex":
        """Stream the gzipped AlphaMissense TSV, keeping only ``target_keys``.

        The hg38 distribution is a tab-separated, ``#``-commented file with columns
        ``CHROM POS REF ALT ... am_pathogenicity am_class``; CHROM is ``chr``-prefixed.
        We never extract the whole file; we read it line-by-line and retain only loci
        of interest, taking the MAX score on a collision. ``target_keys`` are canonical
        :func:`variant_key` strings; pass None to retain everything (large!).
        """
        wanted: Optional[Set[str]] = set(target_keys) if target_keys is not None else None
        scores: Dict[str, float] = {}
        with gzip.open(tsv_gz_path, "rt", encoding="utf-8") as fh:
            for n, line in enumerate(fh):
                if not line or line[0] == "#":
                    continue
                if progress is not None and n and n % 10_000_000 == 0:
                    progress(n, len(scores))
                parts = line.rstrip("\n").split("\t")
                if len(parts) < 9:
                    continue
                chrom, pos, ref, alt = parts[0], parts[1], parts[2], parts[3]
                try:
                    key = variant_key(chrom, int(pos), ref, alt)
                    score = float(parts[8])
                except (ValueError, IndexError):
                    continue
                if wanted is not None and key not in wanted:
                    continue
                prev = scores.get(key)
                if prev is None or score > prev:
                    scores[key] = score
        return cls(scores)

    # -- persistence -------------------------------------------------------- #
    def _payload(self) -> Dict[str, Any]:
        """The byte-stable cache payload (sorted scores), shared by both writers."""
        return {
            "provider": PROVIDER_NAME,
            "version": PROVIDER_VERSION,
            "scores": dict(sorted(self._scores.items())),
        }

    def to_cache(self, path: str = DEFAULT_CACHE_PATH) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self._payload(), f, indent=2, sort_keys=True)
            f.write("\n")

    def to_cache_with_manifest(
        self,
        path: str = DEFAULT_CACHE_PATH,
        *,
        access_date: str,
        source: str = SOURCE_LABEL,
        source_version: str = PROVIDER_VERSION,
        source_url: str = SOURCE_URL,
        notes: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Write the cache + a provenance manifest, byte-stably (job1 task 2).

        Records the source/version/checksum/access-date next to the cache file as
        ``<cache>.manifest.json``. Rebuilding from the same scores produces a
        byte-identical cache (and therefore an identical recorded checksum).
        ``access_date`` is the ISO date the source was read; it is explicit (not the
        wall clock) so the build is deterministic and offline-testable.
        """
        return cache_manifest.write_cache(
            self._payload(), path,
            provider=PROVIDER_NAME, source=source, source_version=source_version,
            access_date=access_date, source_url=source_url,
            record_count=len(self._scores), notes=notes,
        )

    # -- inspection / lookup ------------------------------------------------ #
    def __len__(self) -> int:
        return len(self._scores)

    def __contains__(self, key: str) -> bool:
        return key in self._scores

    def lookup(self, chrom: Any, pos: Any, ref: Any, alt: Any) -> Optional[float]:
        return self._scores.get(variant_key(chrom, pos, ref, alt))


class AlphaMissenseProvider(EvidenceProvider):
    """Resolve an AlphaMissense score for a locus and emit the engine's PP3/BP4 events."""

    name = PROVIDER_NAME
    version = PROVIDER_VERSION

    def __init__(self, index: AlphaMissenseIndex) -> None:
        self.index = index
        self.stats = ProviderStats()

    @classmethod
    def from_cache(cls, path: str = DEFAULT_CACHE_PATH) -> "AlphaMissenseProvider":
        return cls(AlphaMissenseIndex.from_cache(path))

    @classmethod
    def from_scores(cls, scores: Dict[str, float]) -> "AlphaMissenseProvider":
        return cls(AlphaMissenseIndex.from_scores(scores))

    def fetch(self, case_or_variant: Any) -> EvidenceBundle:
        self.stats.queried += 1
        provider_versions = {self.name: self.version}
        loc = locus_of(case_or_variant)

        if loc is None:
            self.stats.failed += 1
            return EvidenceBundle(
                variant_key=None, events=[], provider_versions=provider_versions,
                source_records=[], warnings=["missing_locus"],
                match={"alphamissense_match": False, "variant_key": None})

        chrom, pos, ref, alt = loc
        key = variant_key(chrom, pos, ref, alt)
        score = self.index.lookup(chrom, pos, ref, alt)

        if score is None:
            self.stats.absent += 1
            return EvidenceBundle(
                variant_key=key, events=[], provider_versions=provider_versions,
                source_records=[], warnings=["no_alphamissense_score"],
                match={"alphamissense_match": False, "match_type": "canonical_locus",
                       "variant_key": key, "alphamissense_score": None})

        self.stats.matched += 1
        self.stats.cached += 1
        # Route the score through the SAME engine mapping the fixtures use, so a score
        # fetched here reproduces the engine's threshold bins exactly.
        events = derive_criteria_from_signals({"alphamissense": score})
        warnings: List[str] = []
        if not events:
            warnings.append("alphamissense_ambiguous_band")
        source_records = [{
            "source": "AlphaMissense", "dataset": self.version, "variant_key": key,
            "chrom": chrom, "grch38_pos": pos, "ref": ref, "alt": alt,
            "alphamissense_score": score,
        }]
        match = {
            "alphamissense_match": True, "match_type": "canonical_locus",
            "variant_key": key, "canonical_key": _canonical_key(chrom, pos, ref, alt),
            "alphamissense_score": score, "actionable": bool(events),
        }
        return EvidenceBundle(
            variant_key=key, events=events, provider_versions=provider_versions,
            source_records=source_records, warnings=warnings, match=match)
