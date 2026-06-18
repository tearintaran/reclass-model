"""Conservation and gene-constraint computational providers (gap.md A3).

Two providers that complement the AlphaMissense/REVEL missense predictors:

  * :class:`ConservationProvider` -- a per-position phyloP lookup (small JSON cache,
    same offline pattern as REVEL/AlphaMissense) routed through the engine's
    conservation bins -> a *supporting* PP3 / BP4 event.
  * :class:`GeneConstraintProvider` -- a per-gene gnomAD constraint lookup (LOEUF /
    pLI / regional missense Z). This is a CONTEXT modifier, not a scored criterion:
    it classifies a gene as LoF-constrained and/or missense-constrained so the PVS1
    mechanism check and the REVEL+AlphaMissense consensus can use that context. It
    deliberately emits NO ``EvidenceEvent`` -- gene constraint is not, on its own, an
    ACMG point -- so its harness delta is zero by design.

The multi-predictor resolution itself lives in :func:`engine.scoring.resolve_missense_consensus`
and is re-exported here for convenience. All thresholds live in
``engine/configs/computational_ext_v1.json`` (never base_v1.json), so these providers
add no governed config and perturb no existing reconstruction hash. ``fetch`` is pure
given a fixed cache snapshot and never raises on "no data".
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from engine.scoring import (
    derive_criteria_from_signals,
    load_computational_ext,
    resolve_missense_consensus,  # noqa: F401  (re-exported for callers)
)

from . import cache_manifest
from .model import EvidenceBundle
from .providers import EvidenceProvider
from .revel import locus_of

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
_CACHE_DIR = os.path.join(_ROOT, "data", "cache", "providers")
CONSERVATION_CACHE_PATH = os.path.join(_CACHE_DIR, "conservation_cache.json")
CONSTRAINT_CACHE_PATH = os.path.join(_CACHE_DIR, "gene_constraint_cache.json")

#: Source identity for the cache manifests (job1 task 2).
CONSERVATION_SOURCE = "phyloP 100-way vertebrate conservation (UCSC)"
CONSERVATION_SOURCE_URL = "https://hgdownload.soe.ucsc.edu/goldenPath/hg38/phyloP100way/"
CONSTRAINT_SOURCE = "gnomAD gene constraint (LOEUF / pLI / regional missense Z)"
CONSTRAINT_SOURCE_URL = "https://gnomad.broadinstitute.org/downloads#v4-constraint"


def _position_key(chrom: Any, pos: Any) -> str:
    """Position key ``chrom-pos`` (build-stripped) for a per-base conservation score."""
    c = str(chrom).strip()
    if c[:3].lower() == "chr":
        c = c[3:]
    return f"{c}-{int(pos)}"


# --------------------------------------------------------------------------- #
# Conservation (phyloP)                                                        #
# --------------------------------------------------------------------------- #
class ConservationProvider(EvidenceProvider):
    """Per-position phyloP -> supporting PP3 / BP4 via the engine's conservation bins."""

    name = "conservation"
    version = "phyloP_v1"

    def __init__(self, scores: Optional[Dict[str, float]] = None) -> None:
        self._scores: Dict[str, float] = dict(scores or {})

    @classmethod
    def from_scores(cls, scores: Dict[str, float]) -> "ConservationProvider":
        return cls(scores)

    @classmethod
    def from_cache(cls, path: str = CONSERVATION_CACHE_PATH) -> "ConservationProvider":
        if not os.path.exists(path):
            return cls({})
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return cls(dict(data.get("scores") or {}))

    def _payload(self) -> Dict[str, Any]:
        return {"provider": self.name, "version": self.version,
                "scores": dict(sorted(self._scores.items()))}

    def to_cache(self, path: str = CONSERVATION_CACHE_PATH) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self._payload(), f, indent=2, sort_keys=True)
            f.write("\n")

    def to_cache_with_manifest(
        self, path: str = CONSERVATION_CACHE_PATH, *, access_date: str,
        source: str = CONSERVATION_SOURCE, source_version: Optional[str] = None,
        source_url: str = CONSERVATION_SOURCE_URL, notes: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Write the conservation cache + provenance manifest, byte-stably (task 2)."""
        return cache_manifest.write_cache(
            self._payload(), path, provider=self.name, source=source,
            source_version=source_version or self.version, access_date=access_date,
            source_url=source_url, record_count=len(self._scores), notes=notes,
        )

    def lookup(self, chrom: Any, pos: Any) -> Optional[float]:
        return self._scores.get(_position_key(chrom, pos))

    def fetch(self, case_or_variant: Any) -> EvidenceBundle:
        provider_versions = {self.name: self.version}
        loc = locus_of(case_or_variant)
        if loc is None:
            return EvidenceBundle(
                variant_key=None, events=[], provider_versions=provider_versions,
                source_records=[], warnings=["missing_locus"],
                match={"conservation_match": False})
        chrom, pos, ref, alt = loc
        key = _position_key(chrom, pos)
        phylop = self.lookup(chrom, pos)
        if phylop is None:
            return EvidenceBundle(
                variant_key=key, events=[], provider_versions=provider_versions,
                source_records=[], warnings=["no_conservation_score"],
                match={"conservation_match": False, "position_key": key, "phylop": None})
        events = derive_criteria_from_signals({"conservation": phylop})
        warnings: List[str] = [] if events else ["conservation_indeterminate_band"]
        return EvidenceBundle(
            variant_key=key, events=events, provider_versions=provider_versions,
            source_records=[{"source": "phyloP", "position_key": key, "phylop": phylop}],
            warnings=warnings,
            match={"conservation_match": bool(events), "position_key": key,
                   "phylop": phylop, "actionable": bool(events)})


# --------------------------------------------------------------------------- #
# Gene-level constraint (context modifier; emits no points)                   #
# --------------------------------------------------------------------------- #
def classify_constraint(metrics: Dict[str, Any], comp: Optional[Dict[str, Any]] = None) -> Dict[str, bool]:
    """Classify gene constraint from LOEUF/pLI/missense-Z (context, not a criterion).

    A gene is LoF-constrained when its LOEUF is at/below ``loeuf_constrained`` OR its
    pLI is at/above ``pli_constrained``; missense-constrained when its regional
    missense Z is at/above ``missense_z_constrained``. Missing metrics are treated as
    "not constrained" for that axis (never guessed).
    """
    comp = comp if comp is not None else load_computational_ext()
    cfg = comp.get("constraint") or {}
    loeuf = metrics.get("loeuf")
    pli = metrics.get("pli")
    mz = metrics.get("missense_z")
    lof = ((loeuf is not None and float(loeuf) <= float(cfg.get("loeuf_constrained", 0.0)))
           or (pli is not None and float(pli) >= float(cfg.get("pli_constrained", 1.0))))
    missense = mz is not None and float(mz) >= float(cfg.get("missense_z_constrained", 99.0))
    return {"lof_constrained": bool(lof), "missense_constrained": bool(missense)}


class GeneConstraintProvider(EvidenceProvider):
    """Per-gene gnomAD constraint context (LOEUF/pLI/missense-Z). Emits no ACMG points."""

    name = "gene_constraint"
    version = "gnomAD_constraint_v1"

    def __init__(self, by_gene: Optional[Dict[str, Dict[str, Any]]] = None,
                 comp: Optional[Dict[str, Any]] = None) -> None:
        self._by_gene = {str(g).upper(): dict(m) for g, m in (by_gene or {}).items()}
        self._comp = comp if comp is not None else load_computational_ext()

    @classmethod
    def from_metrics(cls, by_gene: Dict[str, Dict[str, Any]]) -> "GeneConstraintProvider":
        return cls(by_gene)

    @classmethod
    def from_cache(cls, path: str = CONSTRAINT_CACHE_PATH) -> "GeneConstraintProvider":
        if not os.path.exists(path):
            return cls({})
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return cls(dict(data.get("genes") or {}))

    def _payload(self) -> Dict[str, Any]:
        return {"provider": self.name, "version": self.version,
                "genes": {g: m for g, m in sorted(self._by_gene.items())}}

    def to_cache(self, path: str = CONSTRAINT_CACHE_PATH) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self._payload(), f, indent=2, sort_keys=True)
            f.write("\n")

    def to_cache_with_manifest(
        self, path: str = CONSTRAINT_CACHE_PATH, *, access_date: str,
        source: str = CONSTRAINT_SOURCE, source_version: Optional[str] = None,
        source_url: str = CONSTRAINT_SOURCE_URL, notes: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Write the gene-constraint cache + provenance manifest, byte-stably (task 2)."""
        return cache_manifest.write_cache(
            self._payload(), path, provider=self.name, source=source,
            source_version=source_version or self.version, access_date=access_date,
            source_url=source_url, record_count=len(self._by_gene), notes=notes,
        )

    def metrics_for(self, gene: Optional[str]) -> Optional[Dict[str, Any]]:
        return self._by_gene.get(str(gene).upper()) if gene else None

    @staticmethod
    def _gene_of(case_or_variant: Any) -> Optional[str]:
        if isinstance(case_or_variant, dict):
            return case_or_variant.get("gene")
        if isinstance(case_or_variant, str):
            return case_or_variant
        return None

    def fetch(self, case_or_variant: Any) -> EvidenceBundle:
        provider_versions = {self.name: self.version}
        gene = self._gene_of(case_or_variant)
        metrics = self.metrics_for(gene)
        if metrics is None:
            return EvidenceBundle(
                variant_key=None, events=[], provider_versions=provider_versions,
                source_records=[], warnings=["no_constraint_data"],
                match={"gene_constraint_match": False, "gene": gene})
        classification = classify_constraint(metrics, self._comp)
        # Context only: NO events. The classification informs PVS1 mechanism / consensus.
        return EvidenceBundle(
            variant_key=None, events=[], provider_versions=provider_versions,
            source_records=[{"source": "gnomAD_constraint", "gene": gene, **metrics}],
            warnings=["constraint_context_only"],
            match={"gene_constraint_match": True, "gene": str(gene).upper(),
                   "metrics": dict(metrics), **classification})
