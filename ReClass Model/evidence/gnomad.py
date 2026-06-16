"""gnomAD allele-frequency evidence provider (targeted, cached, provenance-rich).

Downloading all of gnomAD v4.1 is infeasible locally (terabytes). The one-off
`ingest/enrich_gnomad.py` script did the right thing -- a TARGETED GraphQL lookup of
only the benchmark loci -- but held the logic inline and wrote AFs straight back into
the committed fixture. This module turns that into a reusable
:class:`~evidence.providers.EvidenceProvider`:

  * :class:`GnomadCache`    -- responses keyed by variant id, each entry recording the
                               **source version, timestamp, query id, and
                               absent-vs-failed status** distinctly,
  * :class:`GnomadProvider` -- requests ``joint.faf95.popmax`` (the popmax filtering
                               AF gnomAD recommends for BA1/BS1/PM2) with a
                               genome/exome AF fallback, then emits the same
                               BA1/BS1/PM2 :class:`EvidenceEvent`s the engine derives
                               from ``signals.gnomad_af``.

Two invariants the legacy script blurred and this provider keeps crisp:

  * **Absence != AF 0.** A variant missing from gnomAD is *unknown* frequency
    evidence (warning ``gnomad_absent``), never allele frequency zero.
  * **Absent != failed.** A genuine "not in gnomAD" answer is cached and distinct
    from a transport/query failure (``gnomad_query_failed``), which can be retried.

`fetch` is deterministic for a fixed cache snapshot (cache hits replay the stored
timestamp/query id and never touch the network); tests run fully offline by
injecting a fake fetcher or a pre-populated cache.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from engine.scoring import derive_criteria_from_signals
from engine.normalize import add_build as _add_build

from .model import EvidenceBundle
from .providers import EvidenceProvider

#: Stable provider identity / source dataset version.
PROVIDER_NAME = "gnomad"
PROVIDER_VERSION = "gnomad_r4"
DATASET = "gnomad_r4"

API = "https://gnomad.broadinstitute.org/api"

#: We request popmax FAF (plus its population) and raw genome/exome AF for fallback.
QUERY = (
    '{variant(variantId:"%s",dataset:gnomad_r4)'
    "{genome{af}exome{af}joint{faf95{popmax popmax_population}}}}"
)

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
DEFAULT_CACHE_PATH = os.path.join(_ROOT, "data", "cache", "providers", "gnomad_cache.json")

#: A fetcher takes a gnomAD variant id and returns one of:
#:   {"status": "matched", "variant": <raw variant payload>}
#:   {"status": "absent",  "variant": None}   # genuinely not in gnomAD
#:   {"status": "failed",  "variant": None}   # transport / GraphQL error -> retryable
Fetcher = Callable[[str], Dict[str, Any]]


def variant_id_of(case_or_variant: Any) -> Optional[str]:
    """Build a gnomAD ``"chrom-pos-ref-alt"`` variant id from a case/variant.

    Accepts a fixture case dict (``locus`` block), a bare ``{chrom,pos,ref,alt}``
    dict, a ``(chrom, pos, ref, alt)`` tuple/list, or an existing id string.
    """
    src: Any = case_or_variant
    if isinstance(src, str):
        return src if src.count("-") == 3 else None
    if isinstance(src, (tuple, list)) and len(src) == 4:
        try:
            return f"{src[0]}-{int(src[1])}-{src[2]}-{src[3]}"
        except (TypeError, ValueError):
            return None
    if isinstance(src, dict):
        loc = src.get("locus", src)
        try:
            return f"{loc['chrom']}-{int(loc['pos'])}-{loc['ref']}-{loc['alt']}"
        except (KeyError, TypeError, ValueError):
            return None
    return None


@dataclass
class ProviderStats:
    """Per-run provider call statistics (gap.md section 1, task 6).

    ``matched + absent + failed == queried`` (every fetch lands in exactly one
    outcome bucket); ``cached`` counts the subset served without a network call.
    """

    queried: int = 0
    matched: int = 0
    absent: int = 0
    failed: int = 0
    cached: int = 0

    def as_dict(self) -> Dict[str, int]:
        return asdict(self)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def curl_fetcher(variant_id: str) -> Dict[str, Any]:
    """Default network fetcher: one targeted gnomAD GraphQL call via ``curl``.

    curl uses the OS certificate store (macOS system Python often can't verify TLS
    on its own). Returns the ``{status, variant}`` contract; never raises.
    """
    payload = json.dumps({"query": QUERY % variant_id})
    try:
        out = subprocess.run(
            ["curl", "-s", "--max-time", "30", API,
             "-H", "Content-Type: application/json", "-d", payload],
            capture_output=True, text=True, timeout=40,
        ).stdout
        doc = json.loads(out)
    except Exception:
        return {"status": "failed", "variant": None}
    if doc.get("errors"):
        return {"status": "failed", "variant": None}
    variant = (doc.get("data") or {}).get("variant")
    if not variant:
        return {"status": "absent", "variant": None}
    return {"status": "matched", "variant": variant}


def _parse_variant(variant: Dict[str, Any]) -> Dict[str, Any]:
    """Resolve the recommended AF + provenance from a raw gnomAD variant payload.

    Mirrors the legacy `fetch_af`: prefer ``joint.faf95.popmax``; otherwise fall back
    to the larger raw genome/exome AF. Population, both raw AFs, and which path was
    used are all preserved so the source record is fully auditable.
    """
    joint_faf = (variant.get("joint") or {}).get("faf95") or {}
    faf_popmax = joint_faf.get("popmax")
    faf_pop = joint_faf.get("popmax_population")
    genome_af = (variant.get("genome") or {}).get("af")
    exome_af = (variant.get("exome") or {}).get("af")

    if faf_popmax is not None:
        af: Optional[float] = float(faf_popmax)
        af_source: Optional[str] = "faf95_popmax"
        population: Optional[str] = faf_pop
    else:
        afs = [float(a) for a in (genome_af, exome_af) if a is not None]
        if afs:
            af, af_source, population = max(afs), "genome_exome_fallback", None
        else:
            af, af_source, population = None, None, None

    return {
        "af": af,
        "af_source": af_source,
        "population": population,
        "faf95_popmax": faf_popmax,
        "faf95_population": faf_pop,
        "genome_af": genome_af,
        "exome_af": exome_af,
    }


class GnomadCache:
    """gnomAD responses keyed by variant id, persisted as a small local JSON file."""

    def __init__(self, entries: Optional[Dict[str, Dict[str, Any]]] = None) -> None:
        self._entries: Dict[str, Dict[str, Any]] = dict(entries or {})

    @classmethod
    def from_cache(cls, path: str = DEFAULT_CACHE_PATH) -> "GnomadCache":
        if not os.path.exists(path):
            return cls({})
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return cls(dict(data.get("entries") or {}))

    def to_cache(self, path: str = DEFAULT_CACHE_PATH) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        payload = {
            "provider": PROVIDER_NAME,
            "version": PROVIDER_VERSION,
            "entries": dict(sorted(self._entries.items())),
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, sort_keys=True)
            f.write("\n")

    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, variant_id: str) -> bool:
        return variant_id in self._entries

    def get(self, variant_id: str) -> Optional[Dict[str, Any]]:
        entry = self._entries.get(variant_id)
        return dict(entry) if entry is not None else None

    def put(self, variant_id: str, entry: Dict[str, Any]) -> None:
        self._entries[variant_id] = dict(entry)


class GnomadProvider(EvidenceProvider):
    """Resolve a gnomAD popmax FAF for a variant and emit BA1/BS1/PM2 events."""

    name = PROVIDER_NAME
    version = PROVIDER_VERSION

    def __init__(
        self,
        cache: Optional[GnomadCache] = None,
        fetcher: Optional[Fetcher] = None,
        *,
        retry_failed: bool = False,
        clock: Optional[Callable[[], str]] = None,
    ) -> None:
        #: ``fetcher=None`` => offline: cache misses resolve to a deterministic
        #: ``gnomad_not_cached`` failure instead of touching the network.
        self.cache = cache if cache is not None else GnomadCache()
        self.fetcher = fetcher
        self.retry_failed = retry_failed
        self._clock = clock or _utcnow_iso
        self.stats = ProviderStats()

    @classmethod
    def offline(cls, path: str = DEFAULT_CACHE_PATH) -> "GnomadProvider":
        """A provider backed purely by an existing cache snapshot (no network)."""
        return cls(GnomadCache.from_cache(path), fetcher=None)

    @staticmethod
    def _query_id(variant_id: str) -> str:
        return hashlib.sha1((QUERY % variant_id).encode("utf-8")).hexdigest()[:16]

    def _empty_entry(self, variant_id: str, status: str) -> Dict[str, Any]:
        return {
            "variant_id": variant_id,
            "status": status,
            "dataset": DATASET,
            "version": self.version,
            "query_id": self._query_id(variant_id),
            "timestamp": self._clock(),
            "af": None,
            "af_source": None,
            "population": None,
            "faf95_popmax": None,
            "faf95_population": None,
            "genome_af": None,
            "exome_af": None,
        }

    def _query(self, variant_id: str) -> Optional[Dict[str, Any]]:
        """Resolve a fresh cache entry via the fetcher, or None when offline."""
        if self.fetcher is None:
            return None
        result = self.fetcher(variant_id)
        status = result.get("status")
        entry = self._empty_entry(variant_id, status if status in ("matched", "absent", "failed") else "failed")
        if entry["status"] == "matched":
            entry.update(_parse_variant(result.get("variant") or {}))
        return entry

    def fetch(self, case_or_variant: Any) -> EvidenceBundle:
        self.stats.queried += 1
        pv = {self.name: self.version}
        vid = variant_id_of(case_or_variant)

        if vid is None:
            self.stats.failed += 1
            return EvidenceBundle(
                variant_key=None,
                events=[],
                provider_versions=pv,
                source_records=[],
                warnings=["missing_locus"],
                match={"gnomad_match": False, "variant_id": None, "status": "failed"},
            )

        cached = self.cache.get(vid)
        can_retry = self.retry_failed and self.fetcher is not None
        use_cache = cached is not None and not (can_retry and cached.get("status") == "failed")

        if use_cache:
            self.stats.cached += 1
            entry = cached
        else:
            entry = self._query(vid)
            if entry is None:
                # Offline and not cached: deterministic, non-poisoning failure.
                self.stats.failed += 1
                return EvidenceBundle(
                    variant_key=vid,
                    events=[],
                    provider_versions=pv,
                    source_records=[],
                    warnings=["gnomad_not_cached"],
                    match={"gnomad_match": False, "variant_id": vid, "status": "failed"},
                )
            self.cache.put(vid, entry)

        status = entry["status"]
        if status == "matched":
            self.stats.matched += 1
        elif status == "absent":
            self.stats.absent += 1
        else:
            self.stats.failed += 1

        return self._bundle_from_entry(vid, entry, pv)

    def _bundle_from_entry(
        self, vid: str, entry: Dict[str, Any], pv: Dict[str, str]
    ) -> EvidenceBundle:
        status = entry["status"]
        provenance = dict(entry)

        canonical = _add_build(vid)  # storage-form canonical key (build-prefixed)

        if status == "failed":
            return EvidenceBundle(
                variant_key=vid,
                events=[],
                provider_versions=pv,
                source_records=[provenance],
                warnings=["gnomad_query_failed"],
                match={"gnomad_match": False, "match_type": "canonical_locus",
                       "variant_id": vid, "canonical_key": canonical, "status": "failed",
                       "dataset": entry["dataset"]},
            )

        if status == "absent":
            # Unknown frequency evidence -- explicitly NOT allele frequency 0.
            return EvidenceBundle(
                variant_key=vid,
                events=[],
                provider_versions=pv,
                source_records=[provenance],
                warnings=["gnomad_absent"],
                match={"gnomad_match": False, "match_type": "canonical_locus",
                       "variant_id": vid, "canonical_key": canonical, "status": "absent",
                       "dataset": entry["dataset"]},
            )

        # matched
        af = entry["af"]
        warnings: List[str] = []
        if af is None:
            events: List[Any] = []
            warnings.append("gnomad_af_unavailable")
        else:
            events = derive_criteria_from_signals({"gnomad_af": float(af)})
            if entry.get("af_source") == "genome_exome_fallback":
                warnings.append("gnomad_faf95_unavailable_used_af_fallback")
            if not events:
                warnings.append("gnomad_af_indeterminate")

        match = {
            "gnomad_match": True,
            "match_type": "canonical_locus",
            "variant_id": vid,
            "canonical_key": canonical,
            "status": "matched",
            "dataset": entry["dataset"],
            "af": af,
            "af_source": entry.get("af_source"),
            "population": entry.get("population"),
            "actionable": bool(events),
        }
        return EvidenceBundle(
            variant_key=vid,
            events=events,
            provider_versions=pv,
            source_records=[provenance],
            warnings=warnings,
            match=match,
        )
