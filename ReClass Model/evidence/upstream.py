"""Validated upstream evidence adapters (job1 task 1).

The base evidence slice covered ClinGen-applied criteria, REVEL/AlphaMissense, and
gnomAD frequency. This module adds reusable, provenance-rich adapters for the
*upstream, case-level* evidence types an ACMG/AMP interpretation rests on but that no
single public score encodes:

  * :class:`DeNovoAdapter`          -- confirmed/assumed de novo occurrence -> PS2 / PM6
  * :class:`PhasingAdapter`         -- trans/cis phase w.r.t. a known variant -> PM3 / BP2
  * :class:`SegregationAdapter`     -- informative meioses -> PP1 / BS4
  * :class:`PhenotypeAdapter`       -- phenotype specificity -> PP4
  * :class:`FunctionalAssayAdapter` -- well-established assay readout -> PS3 / BS3
  * :class:`DiseaseMechanismAdapter`-- gene disease-mechanism context -> PP2 / BP1
  * :class:`CaseControlAdapter`     -- case-control enrichment (odds ratio) -> PS4

Every adapter honors three contracts that distinguish *validated* upstream evidence
from a guess (job1 task 1):

  1. **Recorded provenance.** Each emitted (and each absent) record carries the
     ``source``, ``source_version``, a content **checksum** (SHA-256 of the source
     record), and the **access date** it was read. Provenance lives in
     ``source_records`` and in ``event.raw`` -- outside the engine reconstruction
     hash -- so attribution never perturbs a classification.
  2. **Deterministic no-call on absence.** A missing evidence type yields an explicit
     *absent* record (``status="absent"``, ``called=False``) -- never allele-frequency
     0, never a default criterion. A present-but-non-actionable signal yields a
     *present_no_call* record. Malformed input yields a *malformed* record. In all
     three cases no ``EvidenceEvent`` is emitted and the outcome is auditable.
  3. **Schema conformance.** Output is a standard :class:`~evidence.model.EvidenceBundle`;
     the case-control adapter additionally populates the bundle's
     :class:`~evidence.model.CohortCounts` (job1 task 5).

Thresholds are reviewable defaults in :data:`UPSTREAM_DEFAULTS` (segregation,
phenotype, and functional reuse the committed ``coverage_ext_v1.json`` bins so they
stay byte-identical to :mod:`evidence.criteria_ext`). ``fetch`` is pure given a fixed
config and never raises -- tests run fully offline against in-memory fixtures.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Iterable, List, Optional, Tuple

from engine.scoring import (
    EvidenceEvent,
    _bin_lookup,
    _functional_to_event,
    _phenotype_to_event,
    _segregation_to_event,
    load_coverage_ext,
)
from engine.normalize import canonical_key as _canonical_key, locus_from_case

from . import cache_manifest
from .model import CohortCounts, EvidenceBundle, TranscriptIdentity
from .providers import EvidenceProvider

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
_CACHE_DIR = os.path.join(_ROOT, "data", "cache", "providers")
#: Local cache for validated functional/phenotype evidence records (job1 task 2).
FUNCTIONAL_PHENOTYPE_CACHE_PATH = os.path.join(_CACHE_DIR, "functional_phenotype_cache.json")

#: Reviewable derivation thresholds for the upstream evidence types not covered by
#: ``coverage_ext_v1.json``. These are defaults reconstructed from ClinGen SVI / ACMG
#: practice; confirm against the current specifications before clinical use. They live
#: in code (not a governed engine config) because they map *new* criteria the base
#: point model never scored -- the engine still sums the emitted events unchanged.
UPSTREAM_DEFAULTS: Dict[str, Any] = {
    "version": "1.0.0",
    "de_novo": {
        # ClinGen SVI de novo point system: confirmed (both parents) de novo events
        # weigh more than assumed (parentage unconfirmed); accumulate to a strength.
        "points_to_strength": [[4.0, "very_strong"], [2.0, "strong"],
                               [1.0, "moderate"], [0.5, "supporting"]],
        "confirmed_points": 2.0,
        "assumed_points": 1.0,
        "require_phenotype_consistency": True,
    },
    "phasing": {
        "pathogenic_partner": ["pathogenic", "likely_pathogenic"],
        "pm3_strength": "moderate",   # in trans with pathogenic, recessive -> PM3
        "bp2_strength": "supporting",  # in cis, or in trans for a dominant gene -> BP2
    },
    "disease_mechanism": {
        "pp2_strength": "supporting",  # missense in a missense-mechanism gene -> PP2
        "bp1_strength": "supporting",  # missense in a truncating-only gene  -> BP1
        "missense_consequences": ["missense", "missense_variant"],
    },
    "case_control": {
        # Odds-ratio -> PS4 strength. PS4 only fires when the enrichment is
        # statistically significant (CI lower bound > 1 OR p < max_p_value); without a
        # significance signal it is a no-call, never assumed significant.
        "or_to_strength": [[20.0, "strong"], [5.0, "moderate"], [2.0, "supporting"]],
        "min_ci_low": 1.0,
        "max_p_value": 0.05,
    },
}


def _coverage_ext() -> Dict[str, Any]:
    return load_coverage_ext()


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #
def _record_checksum(record: Dict[str, Any]) -> str:
    """Stable SHA-256 over a source record's content (sorted, compact JSON)."""
    import hashlib

    payload = json.dumps(record, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _variant_key_of(case_or_variant: Any) -> Optional[str]:
    """Best-effort canonical key from a case ``locus`` block (None when absent)."""
    loc = locus_from_case(case_or_variant) if isinstance(case_or_variant, dict) else None
    if loc is None:
        return None
    return _canonical_key(*loc)


#: A mapped result: (criterion, direction, strength, extra_raw). ``None`` => no-call.
Mapped = Tuple[str, str, str, Dict[str, Any]]


class UpstreamEvidenceAdapter(EvidenceProvider):
    """Base class for the validated upstream evidence adapters (job1 task 1).

    A subclass declares ``evidence_type`` (the key it reads from a case's ``evidence``
    or ``signals`` block) and implements :meth:`_map`. The base handles record
    extraction, provenance (source/version/checksum/access-date), the absent /
    present-no-call / malformed no-call outcomes, and bundle assembly.
    """

    evidence_type: str = "upstream"
    #: Default source identity; a record may override via its own ``source`` /
    #: ``source_version`` / ``source_url`` fields.
    source: str = "upstream_evidence"
    source_version: str = "upstream_v1"
    source_url: Optional[str] = None

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        *,
        access_date: Optional[str] = None,
    ) -> None:
        self.config = config if config is not None else UPSTREAM_DEFAULTS
        #: The "checked on" date stamped onto absent/no-call records when a source
        #: record does not carry its own ``access_date``. Explicit (not the wall
        #: clock) so adapters stay deterministic and offline-testable.
        self.access_date = access_date

    # -- subclass hooks ----------------------------------------------------- #
    @property
    def name(self) -> str:  # EvidenceProvider identity
        return self.evidence_type

    @property
    def version(self) -> str:
        return self.source_version

    def _cfg(self) -> Dict[str, Any]:
        return self.config.get(self.evidence_type) or {}

    def _map(self, record: Dict[str, Any]) -> Optional[Mapped]:  # pragma: no cover
        raise NotImplementedError

    def _cohort_counts(self, record: Dict[str, Any]) -> Optional[CohortCounts]:
        """Optional per-adapter cohort-count extraction (case-control overrides)."""
        return None

    # -- record extraction -------------------------------------------------- #
    def _record_of(self, case_or_variant: Any) -> Any:
        """Pull this adapter's evidence record from a case, or treat input as record.

        Looks in ``case['evidence'][type]`` first (the dedicated upstream-evidence
        block), then ``case['signals'][type]``. Returns ``None`` (absent) when neither
        carries the key, or the bare input when it is not a case dict.
        """
        if not isinstance(case_or_variant, dict):
            return None if case_or_variant is None else case_or_variant
        for container in ("evidence", "signals"):
            block = case_or_variant.get(container)
            if isinstance(block, dict) and self.evidence_type in block:
                return block[self.evidence_type]
        if self.evidence_type in case_or_variant:
            return case_or_variant[self.evidence_type]
        # A bare record dict (no case wrapper) that already looks like our payload.
        if "evidence" not in case_or_variant and "signals" not in case_or_variant \
                and "locus" not in case_or_variant and case_or_variant:
            return case_or_variant
        return None

    # -- provenance --------------------------------------------------------- #
    def _provenance(
        self, record: Optional[Dict[str, Any]], status: str
    ) -> Dict[str, Any]:
        rec = record if isinstance(record, dict) else None
        access = (rec.get("access_date") if rec else None) or self.access_date
        return {
            "evidence_type": self.evidence_type,
            "source": (rec.get("source") if rec else None) or self.source,
            "source_version": (rec.get("source_version") if rec else None) or self.source_version,
            "source_url": (rec.get("source_url") if rec else None) or self.source_url,
            "access_date": access,
            "checksum_algorithm": "sha256",
            "checksum": _record_checksum(rec) if rec else None,
            "status": status,
            "called": status == "called",
            "record": dict(rec) if rec else None,
        }

    # -- bundle builders ---------------------------------------------------- #
    def _bundle(
        self,
        variant_key: Optional[str],
        record: Optional[Dict[str, Any]],
        status: str,
        *,
        events: Optional[List[EvidenceEvent]] = None,
        warning: Optional[str] = None,
        match_extra: Optional[Dict[str, Any]] = None,
        cohort: Optional[CohortCounts] = None,
    ) -> EvidenceBundle:
        prov = self._provenance(record, status)
        match: Dict[str, Any] = {
            f"{self.evidence_type}_match": status == "called",
            "evidence_type": self.evidence_type,
            "status": status,
            "called": status == "called",
            "source": prov["source"],
            "source_version": prov["source_version"],
            "access_date": prov["access_date"],
            "checksum": prov["checksum"],
        }
        if match_extra:
            match.update(match_extra)
        return EvidenceBundle(
            variant_key=variant_key,
            events=list(events or []),
            provider_versions={self.name: self.version},
            source_records=[prov],
            warnings=[warning] if warning else [],
            match=match,
            cohort_counts=cohort,
        )

    # -- main entry point --------------------------------------------------- #
    def fetch(self, case_or_variant: Any) -> EvidenceBundle:
        variant_key = _variant_key_of(case_or_variant)
        record = self._record_of(case_or_variant)

        # 1) Absent: the evidence type was not provided. Explicit no-call.
        if record is None:
            return self._bundle(variant_key, None, "absent",
                                warning=f"{self.evidence_type}_absent",
                                match_extra={"criterion": None})

        # 2) Malformed: present but the wrong shape -> recorded, never guessed.
        if not isinstance(record, dict):
            return self._bundle(variant_key, {"raw": record}, "malformed",
                                warning=f"{self.evidence_type}_malformed",
                                match_extra={"criterion": None})

        try:
            cohort = self._cohort_counts(record)
            mapped = self._map(record)
        except (TypeError, ValueError, KeyError):
            # Bad field types (e.g. odds_ratio="abc") -> malformed no-call.
            return self._bundle(variant_key, record, "malformed",
                                warning=f"{self.evidence_type}_malformed",
                                match_extra={"criterion": None})

        # 3) Present but non-actionable -> explicit present_no_call (never invented).
        if mapped is None:
            return self._bundle(variant_key, record, "present_no_call",
                                warning=f"{self.evidence_type}_no_call",
                                match_extra={"criterion": None}, cohort=cohort)

        # 4) Called: emit one EvidenceEvent, provenance-stamped.
        criterion, direction, strength, extra_raw = mapped
        prov = self._provenance(record, "called")
        raw = dict(record)
        raw.update(extra_raw)
        raw["provenance"] = {k: prov[k] for k in
                             ("source", "source_version", "access_date", "checksum")}
        event = EvidenceEvent(
            source=self.evidence_type, acmg_criterion=criterion,
            evidence_direction=direction, applied_strength=strength,
            source_version=prov["source_version"], raw=raw,
        )
        return self._bundle(
            variant_key, record, "called", events=[event],
            match_extra={"criterion": criterion, "direction": direction,
                         "strength": strength},
            cohort=cohort,
        )


# --------------------------------------------------------------------------- #
# De novo (PS2 / PM6)                                                          #
# --------------------------------------------------------------------------- #
class DeNovoAdapter(UpstreamEvidenceAdapter):
    """Confirmed/assumed de novo occurrence -> PS2 (confirmed) / PM6 (assumed).

    Record fields: ``confirmed_parentage`` (both biological parents confirmed),
    ``phenotype_consistent`` (the de novo phenotype matches the gene/disease),
    ``observations`` (list of ``{confirmed}`` for multiple de novo events), and an
    optional explicit ``points`` total. A de novo in an inconsistent phenotype is not
    evidence (no-call); strength accumulates via the SVI de novo point system.
    """

    evidence_type = "de_novo"
    source = "clinical_de_novo"
    source_version = "upstream_de_novo_v1"

    def _map(self, record: Dict[str, Any]) -> Optional[Mapped]:
        cfg = self._cfg()
        if cfg.get("require_phenotype_consistency") and record.get("phenotype_consistent") is False:
            return None
        confirmed_pts = float(cfg.get("confirmed_points", 2.0))
        assumed_pts = float(cfg.get("assumed_points", 1.0))
        observations = record.get("observations")
        if record.get("points") is not None:
            points = float(record["points"])
            any_confirmed = bool(record.get("confirmed_parentage") or record.get("confirmed"))
        elif observations:
            points = sum(confirmed_pts if (o or {}).get("confirmed") else assumed_pts
                         for o in observations)
            any_confirmed = any((o or {}).get("confirmed") for o in observations)
        else:
            any_confirmed = bool(record.get("confirmed_parentage") or record.get("confirmed"))
            points = confirmed_pts if any_confirmed else assumed_pts
        strength = _bin_lookup(points, [tuple(b) for b in cfg.get("points_to_strength", [])],
                               descending=True)
        if strength is None:
            return None
        criterion = "PS2" if any_confirmed else "PM6"
        return (criterion, "pathogenic", strength, {"de_novo_points": round(points, 4)})


# --------------------------------------------------------------------------- #
# Phasing (PM3 / BP2)                                                          #
# --------------------------------------------------------------------------- #
class PhasingAdapter(UpstreamEvidenceAdapter):
    """Allelic phase w.r.t. a known variant -> PM3 (recessive in trans) / BP2.

    Record fields: ``phase`` (``trans`` / ``cis`` / ``unknown``),
    ``partner_classification`` (the classification of the variant on the other/same
    allele), ``inheritance`` (``recessive`` / ``dominant``). In trans with a
    pathogenic variant for a recessive gene supports PM3; in cis with a pathogenic
    variant (any inheritance), or in trans for a fully-penetrant dominant gene,
    supports BP2 (benign). Anything else -- unknown phase, benign partner -- is a
    no-call.
    """

    evidence_type = "phasing"
    source = "clinical_phasing"
    source_version = "upstream_phasing_v1"

    def _map(self, record: Dict[str, Any]) -> Optional[Mapped]:
        cfg = self._cfg()
        phase = str(record.get("phase", "")).strip().lower()
        partner = str(record.get("partner_classification", "")).strip().lower()
        inheritance = str(record.get("inheritance", "")).strip().lower()
        pathogenic_partner = {p.lower() for p in cfg.get("pathogenic_partner", [])}
        partner_pathogenic = partner in pathogenic_partner
        if phase == "trans" and partner_pathogenic and inheritance == "recessive":
            return ("PM3", "pathogenic", cfg.get("pm3_strength", "moderate"), {})
        if (phase == "cis" and partner_pathogenic) or (
                phase == "trans" and partner_pathogenic and inheritance == "dominant"):
            return ("BP2", "benign", cfg.get("bp2_strength", "supporting"), {})
        return None


# --------------------------------------------------------------------------- #
# Segregation (PP1 / BS4) -- reuses the coverage_ext meioses bins              #
# --------------------------------------------------------------------------- #
class SegregationAdapter(UpstreamEvidenceAdapter):
    """Informative meioses -> PP1 (cosegregation) / BS4 (non-segregation).

    Record fields: ``meioses`` (informative-meioses count), ``segregates`` (default
    True). Reuses the committed ``coverage_ext_v1.json`` segregation bins so the
    strength is byte-identical to :class:`evidence.criteria_ext.SegregationProvider`,
    but adds the upstream provenance contract (source/version/checksum/access-date).
    """

    evidence_type = "segregation"
    source = "clinical_segregation"
    source_version = "upstream_segregation_v1"

    def _map(self, record: Dict[str, Any]) -> Optional[Mapped]:
        ev = _segregation_to_event(record, _coverage_ext())
        if ev is None:
            return None
        return (ev.acmg_criterion, ev.evidence_direction, ev.applied_strength, {})


# --------------------------------------------------------------------------- #
# Phenotype specificity (PP4)                                                  #
# --------------------------------------------------------------------------- #
class PhenotypeAdapter(UpstreamEvidenceAdapter):
    """Phenotype specificity -> PP4 (None for low / non-specific).

    Record fields: ``specificity`` (``high`` / ``moderate`` / ``low``). Reuses the
    committed ``coverage_ext_v1.json`` PP4 specificity tiers.
    """

    evidence_type = "phenotype"
    source = "clinical_phenotype"
    source_version = "upstream_phenotype_v1"

    def _map(self, record: Dict[str, Any]) -> Optional[Mapped]:
        ev = _phenotype_to_event(record, _coverage_ext())
        if ev is None:
            return None
        return (ev.acmg_criterion, ev.evidence_direction, ev.applied_strength, {})


# --------------------------------------------------------------------------- #
# Functional assay (PS3 / BS3)                                                 #
# --------------------------------------------------------------------------- #
class FunctionalAssayAdapter(UpstreamEvidenceAdapter):
    """Well-established functional assay -> PS3 (damaging) / BS3 (normal).

    Record fields: ``result`` (``damaging`` / ``normal`` / ...), optional
    ``oddspath`` (calibrated OddsPath -> strength bin), optional ``strength``
    override. Reuses the committed ``coverage_ext_v1.json`` functional bins.
    """

    evidence_type = "functional"
    source = "functional_assay"
    source_version = "upstream_functional_v1"

    def _map(self, record: Dict[str, Any]) -> Optional[Mapped]:
        ev = _functional_to_event(record, _coverage_ext())
        if ev is None:
            return None
        return (ev.acmg_criterion, ev.evidence_direction, ev.applied_strength, {})


# --------------------------------------------------------------------------- #
# Disease mechanism (PP2 / BP1)                                                #
# --------------------------------------------------------------------------- #
class DiseaseMechanismAdapter(UpstreamEvidenceAdapter):
    """Gene disease-mechanism context for a missense variant -> PP2 / BP1.

    Record fields: ``consequence`` (e.g. ``missense``), ``missense_mechanism`` (the
    gene has a low benign-missense rate and missense is a common disease mechanism ->
    PP2), ``lof_mechanism`` (truncating/LoF is the gene's mechanism). A missense
    variant in a missense-mechanism gene supports PP2; a missense variant in a
    truncating-only gene supports BP1 (benign). Non-missense consequences and
    ambiguous mechanisms are a no-call (this is *context*, never a guess).
    """

    evidence_type = "disease_mechanism"
    source = "gene_mechanism"
    source_version = "upstream_mechanism_v1"

    def _map(self, record: Dict[str, Any]) -> Optional[Mapped]:
        cfg = self._cfg()
        consequence = str(record.get("consequence", "")).strip().lower()
        is_missense = consequence in {c.lower() for c in cfg.get("missense_consequences", [])}
        missense_mech = bool(record.get("missense_mechanism"))
        lof_mech = bool(record.get("lof_mechanism"))
        if not is_missense:
            return None
        if missense_mech:
            return ("PP2", "pathogenic", cfg.get("pp2_strength", "supporting"), {})
        if lof_mech:
            return ("BP1", "benign", cfg.get("bp1_strength", "supporting"), {})
        return None


# --------------------------------------------------------------------------- #
# Case-control (PS4) -- also populates the bundle's CohortCounts (job1 task 5) #
# --------------------------------------------------------------------------- #
def odds_ratio_from_counts(record: Dict[str, Any]) -> Optional[float]:
    """Compute an odds ratio from a 2x2 case/control table, or None.

    Uses ``(a*d)/(b*c)`` with ``a=case_count``, ``b=case_total-case_count``,
    ``c=control_count``, ``d=control_total-control_count``. Returns None when any
    count is missing or a denominator is zero (never an imputed effect size).
    """
    try:
        a = int(record["case_count"])
        ct = int(record["case_total"])
        c = int(record["control_count"])
        cot = int(record["control_total"])
    except (KeyError, TypeError, ValueError):
        return None
    b = ct - a
    d = cot - c
    if a < 0 or b < 0 or c < 0 or d < 0 or b == 0 or c == 0:
        return None
    return (a * d) / (b * c)


class CaseControlAdapter(UpstreamEvidenceAdapter):
    """Case-control enrichment -> PS4, plus the cohort counts it was derived from.

    Record fields: ``odds_ratio`` (or the 2x2 counts ``case_count`` / ``case_total``
    / ``control_count`` / ``control_total`` to compute it), optional ``ci_low`` /
    ``ci_high`` / ``p_value``. PS4 fires only when the enrichment is statistically
    significant (CI lower bound > ``min_ci_low`` OR ``p_value`` < ``max_p_value``);
    without a significance signal it is a no-call (never assumed significant). The
    cohort counts + computed odds ratio are always recorded on the bundle's
    :class:`~evidence.model.CohortCounts` (job1 task 5), even on a no-call.
    """

    evidence_type = "case_control"
    source = "case_control_cohort"
    source_version = "upstream_case_control_v1"

    def _odds_ratio(self, record: Dict[str, Any]) -> Optional[float]:
        if record.get("odds_ratio") is not None:
            return float(record["odds_ratio"])
        return odds_ratio_from_counts(record)

    def _cohort_counts(self, record: Dict[str, Any]) -> Optional[CohortCounts]:
        def _int(key: str) -> Optional[int]:
            v = record.get(key)
            return None if v is None else int(v)

        def _float(key: str) -> Optional[float]:
            v = record.get(key)
            return None if v is None else float(v)

        odds_ratio = self._odds_ratio(record)
        # Only model a cohort when at least one count or an effect size is present.
        keys = ("case_count", "case_total", "control_count", "control_total")
        if odds_ratio is None and all(record.get(k) is None for k in keys):
            return None
        return CohortCounts(
            case_count=_int("case_count"),
            case_total=_int("case_total"),
            control_count=_int("control_count"),
            control_total=_int("control_total"),
            odds_ratio=odds_ratio,
            ci_low=_float("ci_low"),
            ci_high=_float("ci_high"),
            p_value=_float("p_value"),
            cohort=record.get("cohort"),
            source=record.get("source") or self.source,
        )

    def _is_significant(self, record: Dict[str, Any], cfg: Dict[str, Any]) -> bool:
        ci_low = record.get("ci_low")
        p_value = record.get("p_value")
        if ci_low is None and p_value is None:
            return False  # cannot establish significance -> no-call, never assumed
        by_ci = ci_low is not None and float(ci_low) > float(cfg.get("min_ci_low", 1.0))
        by_p = p_value is not None and float(p_value) < float(cfg.get("max_p_value", 0.05))
        return bool(by_ci or by_p)

    def _map(self, record: Dict[str, Any]) -> Optional[Mapped]:
        cfg = self._cfg()
        odds_ratio = self._odds_ratio(record)
        if odds_ratio is None:
            return None
        if not self._is_significant(record, cfg):
            return None
        strength = _bin_lookup(odds_ratio, [tuple(b) for b in cfg.get("or_to_strength", [])],
                               descending=True)
        if strength is None:
            return None
        return ("PS4", "pathogenic", strength, {"odds_ratio": round(odds_ratio, 6)})


#: All upstream adapters, in job1 task-1 listing order.
UPSTREAM_ADAPTER_CLASSES = (
    DeNovoAdapter,
    PhasingAdapter,
    SegregationAdapter,
    PhenotypeAdapter,
    FunctionalAssayAdapter,
    DiseaseMechanismAdapter,
    CaseControlAdapter,
)


class UpstreamEvidenceProvider(EvidenceProvider):
    """Run every upstream adapter over a case and merge into one EvidenceBundle.

    Convenience for callers that want all upstream evidence at once. Events are
    concatenated in adapter order; per-adapter warnings, provenance source records,
    and the case-control cohort counts are preserved so a reviewer can see which
    evidence types were present, called, no-called, or absent.
    """

    name = "upstream_evidence"
    version = UPSTREAM_DEFAULTS["version"]

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        *,
        access_date: Optional[str] = None,
    ) -> None:
        self.config = config if config is not None else UPSTREAM_DEFAULTS
        self.access_date = access_date
        self._adapters = [cls(self.config, access_date=access_date)
                          for cls in UPSTREAM_ADAPTER_CLASSES]

    def fetch(self, case_or_variant: Any) -> EvidenceBundle:
        variant_key = _variant_key_of(case_or_variant)
        events: List[EvidenceEvent] = []
        source_records: List[Dict[str, Any]] = []
        warnings: List[str] = []
        called: List[str] = []
        provider_versions = {self.name: self.version}
        cohort_counts: Optional[CohortCounts] = None

        for adapter in self._adapters:
            bundle = adapter.fetch(case_or_variant)
            events.extend(bundle.events)
            source_records.extend(bundle.source_records)
            warnings.extend(bundle.warnings)
            provider_versions.update(bundle.provider_versions)
            if bundle.cohort_counts is not None:
                cohort_counts = bundle.cohort_counts
            if bundle.events:
                called.append(adapter.evidence_type)

        return EvidenceBundle(
            variant_key=variant_key,
            events=events,
            provider_versions=provider_versions,
            source_records=source_records,
            warnings=warnings,
            match={"upstream_match": bool(called),
                   "criteria": [e.acmg_criterion for e in events],
                   "evidence_types_called": called},
            transcript=TranscriptIdentity.from_case(case_or_variant),
            cohort_counts=cohort_counts,
        )


def derive_upstream_events(
    case_or_variant: Any,
    config: Optional[Dict[str, Any]] = None,
    *,
    access_date: Optional[str] = None,
) -> List[EvidenceEvent]:
    """Convenience: the merged upstream EvidenceEvents for a case (engine-ready)."""
    return UpstreamEvidenceProvider(config, access_date=access_date).fetch(case_or_variant).events


# --------------------------------------------------------------------------- #
# Validated functional / phenotype source cache (job1 task 2)                  #
# --------------------------------------------------------------------------- #
FUNCTIONAL_PHENOTYPE_PROVIDER = "functional_phenotype"
FUNCTIONAL_PHENOTYPE_VERSION = "functional_phenotype_v1"
FUNCTIONAL_PHENOTYPE_SOURCE = "Validated functional-assay / phenotype-specificity curation"


class FunctionalPhenotypeCache:
    """Local cache of validated functional/phenotype evidence keyed by variant key.

    A reproducible, offline cache builder for the "any validated functional/phenotype
    source" called out in job1 task 2: it streams curated rows (each a
    ``{variant_key, functional?, phenotype?, ...}`` record), keeps only target keys,
    and writes a byte-stable JSON cache plus a provenance manifest (source version,
    checksum, access date). The :class:`FunctionalAssayAdapter` / :class:`PhenotypeAdapter`
    can then resolve a case's evidence from this cache offline. Rebuilding from the
    same rows yields a byte-identical cache (deterministic, MAX-of-duplicates merge).
    """

    def __init__(self, records: Optional[Dict[str, Dict[str, Any]]] = None) -> None:
        self._records: Dict[str, Dict[str, Any]] = {
            str(k): dict(v) for k, v in (records or {}).items()
        }

    @classmethod
    def from_records(cls, records: Dict[str, Dict[str, Any]]) -> "FunctionalPhenotypeCache":
        return cls(records)

    @classmethod
    def build_from_rows(
        cls,
        rows: Iterable[Dict[str, Any]],
        target_keys: Optional[Iterable[str]] = None,
    ) -> "FunctionalPhenotypeCache":
        """Build a cache from curated rows, keeping only ``target_keys`` (or all).

        Each row is a dict with a ``variant_key`` and one or more evidence sub-blocks
        (``functional`` / ``phenotype``). On a duplicate key the later row's blocks are
        merged in deterministically (last-writer-wins per block). Rows without a
        ``variant_key`` are skipped (never keyed under a bogus default).
        """
        wanted = set(target_keys) if target_keys is not None else None
        records: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            key = row.get("variant_key")
            if not key:
                continue
            key = str(key)
            if wanted is not None and key not in wanted:
                continue
            entry = records.setdefault(key, {})
            for block in ("functional", "phenotype"):
                if row.get(block) is not None:
                    entry[block] = dict(row[block]) if isinstance(row[block], dict) else row[block]
        return cls(records)

    @classmethod
    def from_cache(cls, path: str = FUNCTIONAL_PHENOTYPE_CACHE_PATH) -> "FunctionalPhenotypeCache":
        if not os.path.exists(path):
            return cls({})
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return cls(dict(data.get("records") or {}))

    def __len__(self) -> int:
        return len(self._records)

    def __contains__(self, variant_key: str) -> bool:
        return str(variant_key) in self._records

    def lookup(self, variant_key: Optional[str]) -> Optional[Dict[str, Any]]:
        if not variant_key:
            return None
        rec = self._records.get(str(variant_key))
        return dict(rec) if rec is not None else None

    def _payload(self) -> Dict[str, Any]:
        return {
            "provider": FUNCTIONAL_PHENOTYPE_PROVIDER,
            "version": FUNCTIONAL_PHENOTYPE_VERSION,
            "records": {k: self._records[k] for k in sorted(self._records)},
        }

    def to_cache(self, path: str = FUNCTIONAL_PHENOTYPE_CACHE_PATH) -> None:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(cache_manifest.canonical_json(self._payload()))

    def to_cache_with_manifest(
        self,
        path: str = FUNCTIONAL_PHENOTYPE_CACHE_PATH,
        *,
        access_date: str,
        source: str = FUNCTIONAL_PHENOTYPE_SOURCE,
        source_version: str = FUNCTIONAL_PHENOTYPE_VERSION,
        source_url: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Write the functional/phenotype cache + provenance manifest, byte-stably."""
        return cache_manifest.write_cache(
            self._payload(), path,
            provider=FUNCTIONAL_PHENOTYPE_PROVIDER, source=source,
            source_version=source_version, access_date=access_date,
            source_url=source_url, record_count=len(self._records), notes=notes,
        )
