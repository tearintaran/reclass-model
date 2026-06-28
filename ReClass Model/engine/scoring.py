"""Standardized, reproducible ACMG/AMP scoring engine (memo S4 capability 1).

Contract: `classify` is a PURE FUNCTION of (evidence, configuration). No wall-clock,
no randomness, no network, no I/O. The property that matters is not that arithmetic
is deterministic (trivial) but that any historical classification is *exactly
reconstructable* and every point is attributable to either named new evidence or a
named engine version. To make that auditable, every result carries:

  * the full per-criterion contribution breakdown (source + version + points),
  * the engine version,
  * any stand-alone overrides applied,
  * a `reconstruction_hash` over the canonicalized (evidence, engine_version),
    so a stored classification can be re-derived byte-for-byte and verified.

The engine does NOT resolve genuinely uncertain biology -- the judgment lives in
mapping evidence to criteria/strengths (the evidence-integration layer) and in the
mandatory human sign-off downstream (memo S4 capability 4). This module only sums
standardized evidence into a tier.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple

from . import config as C


# --------------------------------------------------------------------------- #
# Evidence model                                                              #
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class EvidenceEvent:
    """One standardized piece of evidence mapped to an ACMG criterion.

    Mirrors research.evidence_events (memo S6.2). `points` is the signed point
    contribution; when omitted it is derived from (direction, applied_strength)
    via the engine config, so callers may pass either an explicit fractional
    contribution OR a named strength.
    """
    source: str                       # clinvar | gnomad | revel | cohort | ...
    acmg_criterion: str               # PVS1 | PS1 | ... | PP3 | BA1 | BP4 | ...
    evidence_direction: str           # pathogenic | benign | neutral
    applied_strength: Optional[str] = None  # very_strong|strong|moderate|supporting|stand_alone
    points: Optional[float] = None    # explicit signed contribution (overrides strength)
    source_version: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)

    def signed_points(self, strength_points: Optional[Dict[str, int]] = None) -> float:
        """Resolve this event's signed point contribution deterministically.

        ``strength_points`` defaults to the base engine config; pass an alternate
        config's mapping (via ``classify(..., config=...)``) to score under a
        different versioned configuration.
        """
        if self.evidence_direction == "neutral":
            return 0.0
        if self.points is not None:
            magnitude = float(self.points)
        else:
            if self.applied_strength is None:
                raise ValueError(
                    f"Evidence {self.acmg_criterion} from {self.source} has neither "
                    f"explicit points nor an applied_strength."
                )
            table = strength_points if strength_points is not None else C.STRENGTH_POINTS
            magnitude = float(table[self.applied_strength])
        sign = 1.0 if self.evidence_direction == "pathogenic" else -1.0
        return sign * abs(magnitude)


@dataclass
class Contribution:
    source: str
    acmg_criterion: str
    evidence_direction: str
    applied_strength: Optional[str]
    points: float
    source_version: Optional[str]


@dataclass
class Classification:
    tier: str
    total_points: float
    contributions: List[Contribution]
    overrides: List[str]
    engine_version: str
    reconstruction_hash: str

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        return d


# --------------------------------------------------------------------------- #
# Deterministic reconstruction hash                                           #
# --------------------------------------------------------------------------- #
def _canonical_evidence(evidence: List[EvidenceEvent]) -> str:
    rows = sorted(
        (
            e.source,
            e.acmg_criterion,
            e.evidence_direction,
            e.applied_strength or "",
            "" if e.points is None else repr(round(float(e.points), 6)),
            e.source_version or "",
        )
        for e in evidence
    )
    return json.dumps(rows, separators=(",", ":"), sort_keys=True)


def reconstruction_hash(evidence: List[EvidenceEvent], engine_version: str) -> str:
    payload = engine_version + "|" + _canonical_evidence(evidence)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# ACMG/AMP single-application normalization                                   #
# --------------------------------------------------------------------------- #
# Source identifiers the engine's OWN computational mappers emit. Anything not in
# this set -- ClinGen/VCEP-applied criteria, hand-curated criteria, cohort evidence
# fed through the `criteria` list or an expert provider -- is treated as
# expert/curated. ACMG/AMP requires each criterion to be applied at most once; when
# the same criterion is supplied by more than one source, an expert strength is not
# overridden by the engine's own computational derivation of the same criterion.
_COMPUTATIONAL_SOURCES = frozenset({
    "revel", "gnomad", "alphamissense", "conservation",
    "revel+alphamissense", "missense_consensus",
    "pvs1", "functional_assay", "in_trans", "segregation", "phenotype",
    "splice", "cnv", "noncoding", "complex_indel", "mito", "repeat", "sv",
})


def _is_curated(event: "EvidenceEvent") -> bool:
    """True for expert/curated criteria (anything not from a computational mapper)."""
    return event.source not in _COMPUTATIONAL_SOURCES


def collapse_single_application(
    evidence: List[EvidenceEvent],
    strength_points: Optional[Dict[str, int]] = None,
) -> Tuple[List[EvidenceEvent], List[str]]:
    """Enforce ACMG/AMP single-application: each criterion contributes at most once.

    When the same ``acmg_criterion`` is emitted more than once (e.g. PP3 from both an
    expert ClinGen curation and the engine's REVEL/conservation derivation), keep ONE
    contribution under a fixed policy: **prefer an expert/curated source**; among
    equally-curated (or all-computational) duplicates keep the strongest by absolute
    points. Without this, two sources agreeing on a criterion would double-count it and
    can flip a tier (the historical defect). Returns the deduplicated events (first
    occurrence preserves order) and human-readable notes for each dropped duplicate.
    """
    def magnitude(e: EvidenceEvent) -> float:
        try:
            return abs(e.signed_points(strength_points))
        except (ValueError, KeyError, TypeError):
            return 0.0

    order: List[str] = []
    groups: Dict[str, List[EvidenceEvent]] = {}
    for e in evidence:
        key = e.acmg_criterion.upper()
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(e)

    kept: List[EvidenceEvent] = []
    dropped_notes: List[str] = []
    for key in order:
        bucket = groups[key]
        if len(bucket) == 1:
            kept.append(bucket[0])
            continue
        curated = [e for e in bucket if _is_curated(e)]
        pool = curated if curated else bucket
        winner = max(pool, key=magnitude)  # ties keep first occurrence (stable max)
        kept.append(winner)
        basis = "expert-curated preferred" if curated else "strongest kept"
        for e in bucket:
            if e is winner:
                continue
            dropped_notes.append(
                f"{key}: dropped duplicate from {e.source} "
                f"({e.applied_strength or 'explicit'}) in favor of {winner.source} "
                f"({winner.applied_strength or 'explicit'}) "
                f"[ACMG single-application: {basis}]"
            )
    return kept, dropped_notes


# --------------------------------------------------------------------------- #
# Classification                                                              #
# --------------------------------------------------------------------------- #
def classify(
    evidence: List[EvidenceEvent],
    engine_version: Optional[str] = None,
    config: "Optional[Any]" = None,
) -> Classification:
    """Sum standardized evidence into an ACMG/AMP tier. Pure and deterministic.

    Evidence is first normalized for **single-application** (each ACMG criterion is
    scored at most once; see :func:`collapse_single_application`) so a criterion
    supplied by both an expert curation and a computational mapper is not
    double-counted. The reconstruction hash is taken over the original (pre-collapse)
    evidence, so a stored classification still re-derives exactly from its inputs.

    Stable public signature: ``classify(evidence)`` and ``classify(evidence,
    engine_version=...)`` behave exactly as before. The optional ``config`` is a
    versioned ``engine.config_registry.EngineConfig``; when given, its strength
    points and tier cutoffs are used (and, unless an explicit ``engine_version`` is
    passed, its fingerprinted ``engine_version`` is recorded, so a config-relevant
    change alters the reconstruction hash). When omitted, the base config is used.
    """
    strength_points = config.strength_points if config is not None else None
    tier_of = config.points_to_tier if config is not None else C.points_to_tier
    if engine_version is None:
        engine_version = config.engine_version if config is not None else C.ENGINE_VERSION

    contributions: List[Contribution] = []
    overrides: List[str] = []

    # ACMG/AMP single-application: a criterion supplied by more than one source is
    # scored once (expert-curated preferred, else strongest). The summed evidence is
    # the deduplicated set; the reconstruction hash below stays over the ORIGINAL
    # input so a stored classification still re-derives byte-for-byte from its inputs.
    scored, dropped_notes = collapse_single_application(evidence, strength_points)
    overrides.extend(dropped_notes)

    # Stand-alone benign rule: BA1 alone classifies Benign (ACMG/AMP 2015).
    has_ba1 = any(
        e.acmg_criterion.upper() == "BA1" and e.evidence_direction == "benign"
        for e in scored
    )

    total = 0.0
    for e in scored:
        pts = e.signed_points(strength_points)
        total += pts
        contributions.append(
            Contribution(
                source=e.source,
                acmg_criterion=e.acmg_criterion,
                evidence_direction=e.evidence_direction,
                applied_strength=e.applied_strength,
                points=round(pts, 4),
                source_version=e.source_version,
            )
        )

    if has_ba1:
        tier = "Benign"
        overrides.append("BA1 stand-alone benign rule applied (overrides point sum).")
    else:
        tier = tier_of(total)

    return Classification(
        tier=tier,
        total_points=round(total, 4),
        contributions=contributions,
        overrides=overrides,
        engine_version=engine_version,
        reconstruction_hash=reconstruction_hash(evidence, engine_version),
    )


# --------------------------------------------------------------------------- #
# Evidence integration: raw signals -> standardized criterion events          #
# --------------------------------------------------------------------------- #
def _revel_to_event(score: float, config: "Optional[Any]" = None) -> Optional[EvidenceEvent]:
    """Map a continuous REVEL score to a PP3 or BP4 event at a calibrated strength."""
    pp3 = config.revel_pp3 if config is not None else C.REVEL_PP3
    bp4 = config.revel_bp4 if config is not None else C.REVEL_BP4
    for threshold, strength in pp3:  # high -> low
        if score >= threshold:
            return EvidenceEvent(
                source="revel", acmg_criterion="PP3", evidence_direction="pathogenic",
                applied_strength=strength, source_version="REVEL",
                raw={"revel_score": score},
            )
    for threshold, strength in bp4:  # low -> high
        if score <= threshold:
            return EvidenceEvent(
                source="revel", acmg_criterion="BP4", evidence_direction="benign",
                applied_strength=strength, source_version="REVEL",
                raw={"revel_score": score},
            )
    return None  # in the indeterminate band -> contributes nothing


def _gnomad_af_to_event(af: float, config: "Optional[Any]" = None) -> Optional[EvidenceEvent]:
    """Map a gnomAD popmax allele frequency to BA1 / BS1 / PM2 deterministically."""
    ba1_af = config.ba1_af if config is not None else C.BA1_AF
    bs1_af = config.bs1_af if config is not None else C.BS1_AF
    pm2_af = config.pm2_af if config is not None else C.PM2_AF
    if af >= ba1_af:
        return EvidenceEvent(source="gnomad", acmg_criterion="BA1",
                             evidence_direction="benign", applied_strength="stand_alone",
                             source_version="gnomAD", raw={"popmax_af": af})
    if af >= bs1_af:
        return EvidenceEvent(source="gnomad", acmg_criterion="BS1",
                             evidence_direction="benign", applied_strength="strong",
                             source_version="gnomAD", raw={"popmax_af": af})
    if af <= pm2_af:
        return EvidenceEvent(source="gnomad", acmg_criterion="PM2",
                             evidence_direction="pathogenic", applied_strength="supporting",
                             source_version="gnomAD", raw={"popmax_af": af})
    return None


# Strengths the caller may name for hand-provided criteria (e.g. PVS1, PS1).
_VALID_STRENGTHS = set(C.STRENGTH_POINTS)


def derive_criteria_from_signals(
    signals: Dict[str, Any], config: "Optional[Any]" = None
) -> List[EvidenceEvent]:
    """Deterministically turn raw signals into standardized EvidenceEvents.

    Recognized signals:
      revel            float REVEL score              -> PP3 / BP4 (combined with AM)
      alphamissense    float AlphaMissense score      -> PP3 / BP4 (combined with REVEL)
      conservation     float phyloP OR {phylop}       -> PP3 / BP4 (supporting)
      gnomad_af        float popmax allele frequency  -> BA1 / BS1 / PM2
      criteria         list of {criterion, direction, strength[, source, version]}
                       pre-mapped expert/curated criteria (PVS1, PS1, PM1, ...)
      <extended>       any coverage-extension key (pvs1, functional, pm3, segregation,
                       phenotype, splice, cnv, noncoding, complex_indel, mito, repeat,
                       sv) -> :func:`derive_extended_criteria` (folded in only when
                       present, via the separate coverage_ext config)

    ``config`` (optional) is a versioned ``EngineConfig`` whose REVEL bins and
    allele-frequency cutoffs are used instead of the base config's -- used by
    threshold-sensitivity analysis. When omitted, behavior is byte-identical to
    the base config.
    """
    events: List[EvidenceEvent] = []

    # Missense in-silico evidence: REVEL and/or AlphaMissense resolve to ONE PP3/BP4
    # (ACMG does not stack predictors). With only REVEL present this is byte-identical
    # to the legacy single-REVEL behavior; with both present
    # ``resolve_missense_consensus`` applies the documented agreement/disagreement rule
    # from the computational config (gap.md A3) instead of emitting two events.
    revel = signals.get("revel")
    alphamissense = signals.get("alphamissense")
    if revel is not None or alphamissense is not None:
        ev = resolve_missense_consensus(
            float(revel) if revel is not None else None,
            float(alphamissense) if alphamissense is not None else None,
            config=config,
        )
        if ev:
            events.append(ev)

    if "gnomad_af" in signals and signals["gnomad_af"] is not None:
        ev = _gnomad_af_to_event(float(signals["gnomad_af"]), config)
        if ev:
            events.append(ev)

    if "conservation" in signals and signals["conservation"] is not None:
        ev = _conservation_to_event(signals["conservation"])
        if ev:
            events.append(ev)

    for c in signals.get("criteria", []):
        strength = c.get("strength")
        if strength is not None and strength not in _VALID_STRENGTHS:
            raise ValueError(f"Unknown strength '{strength}' for criterion {c.get('criterion')}")
        events.append(EvidenceEvent(
            source=c.get("source", "curated"),
            acmg_criterion=c["criterion"],
            evidence_direction=c["direction"],
            applied_strength=strength,
            points=c.get("points"),
            source_version=c.get("version"),
            raw=c.get("raw", {}),
        ))

    # Extended evidence types (job1 task 4 / gap.md A2): pvs1, functional, pm3,
    # segregation, phenotype, splice, cnv, noncoding, complex_indel, mito, repeat, sv.
    # Folded in only when an extended signal key is present, so a base-only signals
    # dict (revel/gnomad_af/criteria) produces byte-identical events to before. These
    # use the SEPARATE coverage_ext config, not the base ``config`` passed for
    # threshold sensitivity, so a config sweep never perturbs them.
    if any(k in signals for k in _EXT_MAPPERS):
        events.extend(derive_extended_criteria(signals))

    return events


def classify_signals(
    signals: Dict[str, Any],
    engine_version: Optional[str] = None,
    config: "Optional[Any]" = None,
) -> Classification:
    """Convenience: signals -> evidence -> classification.

    Passing a versioned ``config`` scores both the signal derivation and the tier
    mapping under that configuration (used by calibration / sensitivity analysis).
    """
    return classify(
        derive_criteria_from_signals(signals, config=config),
        engine_version=engine_version,
        config=config,
    )


# --------------------------------------------------------------------------- #
# Extended evidence coverage (job1 task 4)                                     #
# --------------------------------------------------------------------------- #
# These helpers extend evidence handling BEYOND the base ClinGen/REVEL/gnomAD
# slice -- PVS1, PS3/BS3, PM3, PP1/BS4, PP4, splice, and CNV -- by mapping raw
# extended signals into the engine's standard ``EvidenceEvent``s. They are
# additive: the base ``classify`` / ``derive_criteria_from_signals`` behavior is
# unchanged, the emitted criteria reuse the existing strength keys, and the
# thresholds live in the SEPARATE ``engine/configs/coverage_ext_v1.json`` (never
# in ``base_v1.json``). The reusable ``evidence.criteria_ext`` providers wrap
# these so a score routed through a provider reproduces the same events.

#: Ordering of evidence strengths, weakest -> strongest, for capping/downgrades.
_STRENGTH_ORDER = ("supporting", "moderate", "strong", "very_strong")

_COVERAGE_EXT_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "configs", "coverage_ext_v1.json"
)
_COVERAGE_EXT_CACHE: Optional[Dict[str, Any]] = None


def load_coverage_ext(path: Optional[str] = None) -> Dict[str, Any]:
    """Load the coverage-extension config (cached for the default path).

    The default config is ``engine/configs/coverage_ext_v1.json``; pass ``path``
    to load an alternate (e.g. for tests). Unlike ``base_v1.json`` this config
    only carries derivation thresholds for new evidence types and never affects
    the base point model or reconstruction hashes.
    """
    global _COVERAGE_EXT_CACHE
    if path is not None:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    if _COVERAGE_EXT_CACHE is None:
        with open(_COVERAGE_EXT_PATH, encoding="utf-8") as f:
            _COVERAGE_EXT_CACHE = json.load(f)
    return _COVERAGE_EXT_CACHE


def _ext_version(ext: Dict[str, Any]) -> str:
    return "coverage_ext_v" + str(ext.get("version", "1.0.0"))


def _strength_cap(strength: Optional[str], cap: Optional[str]) -> Optional[str]:
    """Return the weaker of ``strength`` and ``cap`` (either may be None)."""
    if strength is None or cap is None:
        return strength
    if strength not in _STRENGTH_ORDER or cap not in _STRENGTH_ORDER:
        return strength
    return strength if _STRENGTH_ORDER.index(strength) <= _STRENGTH_ORDER.index(cap) else cap


def _bin_lookup(value: float, bins: List[Tuple[float, str]], *, descending: bool) -> Optional[str]:
    """Map a continuous ``value`` to a strength via ordered ``[threshold, strength]`` bins.

    ``descending=True`` for pathogenic-direction scores (higher value -> first bin
    whose threshold ``value`` meets or exceeds); ``descending=False`` for benign
    bins evaluated low -> high (``value <= threshold``).
    """
    for threshold, strength in bins:
        if descending and value >= float(threshold):
            return strength
        if not descending and value <= float(threshold):
            return strength
    return None


def _ext_event(
    source: str, criterion: str, direction: str, strength: Optional[str],
    ext: Dict[str, Any], raw: Dict[str, Any],
) -> Optional[EvidenceEvent]:
    if strength is None:
        return None
    if strength not in _VALID_STRENGTHS:
        raise ValueError(f"coverage_ext produced unknown strength {strength!r} for {criterion}")
    return EvidenceEvent(
        source=source, acmg_criterion=criterion, evidence_direction=direction,
        applied_strength=strength, source_version=_ext_version(ext), raw=dict(raw),
    )


def _pvs1_to_event(signal: Any, ext: Dict[str, Any]) -> Optional[EvidenceEvent]:
    """Map a loss-of-function consequence to a PVS1 event at SVI-tree strength.

    ``signal`` is a consequence string or a dict ``{consequence, lof_mechanism,
    nmd_escape, removes_lt_10pct}``. Returns None when the consequence is not a
    recognised LoF type or (when ``require_lof_mechanism``) the gene's mechanism
    is not loss-of-function -- PVS1 must not fire on a gain-of-function gene.
    """
    cfg = ext.get("pvs1") or {}
    if isinstance(signal, str):
        signal = {"consequence": signal}
    if not isinstance(signal, dict):
        return None
    consequence = str(signal.get("consequence", "")).strip().lower()
    base = (cfg.get("lof_consequences") or {}).get(consequence)
    if base is None:
        return None
    if cfg.get("require_lof_mechanism") and not signal.get("lof_mechanism"):
        return None
    strength = base
    if signal.get("nmd_escape"):
        strength = _strength_cap(strength, cfg.get("nmd_escape_strength"))
    if signal.get("removes_lt_10pct"):
        strength = _strength_cap(strength, cfg.get("removes_lt_10pct_strength"))
    return _ext_event("pvs1", "PVS1", "pathogenic", strength, ext, dict(signal))


def _functional_to_event(signal: Any, ext: Dict[str, Any]) -> Optional[EvidenceEvent]:
    """Map a functional-assay readout to PS3 (damaging) or BS3 (normal/benign)."""
    cfg = ext.get("functional") or {}
    if not isinstance(signal, dict):
        return None
    result = str(signal.get("result", "")).strip().lower()
    oddspath = signal.get("oddspath")
    override = signal.get("strength")
    if result in ("damaging", "abnormal", "pathogenic", "deleterious"):
        strength = override or (
            _bin_lookup(float(oddspath), cfg.get("oddspath_pathogenic") or [], descending=True)
            if oddspath is not None else cfg.get("ps3_default_strength")
        )
        return _ext_event("functional_assay", "PS3", "pathogenic", strength, ext, dict(signal))
    if result in ("normal", "benign", "tolerated", "no_effect"):
        strength = override or (
            _bin_lookup(float(oddspath), cfg.get("oddspath_benign") or [], descending=False)
            if oddspath is not None else cfg.get("bs3_default_strength")
        )
        return _ext_event("functional_assay", "BS3", "benign", strength, ext, dict(signal))
    return None  # intermediate / indeterminate assay -> no criterion


def _pm3_to_event(signal: Any, ext: Dict[str, Any]) -> Optional[EvidenceEvent]:
    """Map in-trans-with-pathogenic observations to a PM3 event (SVI point system)."""
    cfg = ext.get("pm3") or {}
    if not isinstance(signal, dict):
        return None
    if signal.get("points") is not None:
        total = float(signal["points"])
    else:
        values = cfg.get("point_values") or {}
        total = 0.0
        for obs in signal.get("observations") or []:
            total += float(values.get(str((obs or {}).get("type", "")).strip(), 0.0))
    strength = _bin_lookup(total, cfg.get("points_to_strength") or [], descending=True)
    raw = dict(signal)
    raw["pm3_points"] = round(total, 4)
    return _ext_event("in_trans", "PM3", "pathogenic", strength, ext, raw)


def _segregation_to_event(signal: Any, ext: Dict[str, Any]) -> Optional[EvidenceEvent]:
    """Map informative-meioses count to PP1 (segregates) or BS4 (does not)."""
    cfg = ext.get("segregation") or {}
    if not isinstance(signal, dict):
        return None
    raw_meioses = signal.get("meioses")
    if raw_meioses is None:
        return None
    try:
        meioses = int(raw_meioses)
    except (TypeError, ValueError):
        return None
    segregates = signal.get("segregates", True)
    if segregates:
        strength = _bin_lookup(meioses, cfg.get("pp1_meioses") or [], descending=True)
        return _ext_event("segregation", "PP1", "pathogenic", strength, ext, dict(signal))
    strength = _bin_lookup(meioses, cfg.get("bs4_meioses") or [], descending=True)
    return _ext_event("segregation", "BS4", "benign", strength, ext, dict(signal))


def _phenotype_to_event(signal: Any, ext: Dict[str, Any]) -> Optional[EvidenceEvent]:
    """Map phenotype specificity to a PP4 event (None for low/non-specific)."""
    cfg = ext.get("phenotype") or {}
    if isinstance(signal, str):
        signal = {"specificity": signal}
    if not isinstance(signal, dict):
        return None
    spec = str(signal.get("specificity", "")).strip().lower()
    strength = (cfg.get("pp4_specificity") or {}).get(spec)
    return _ext_event("phenotype", "PP4", "pathogenic", strength, ext, dict(signal))


def _splice_to_event(signal: Any, ext: Dict[str, Any]) -> Optional[EvidenceEvent]:
    """Map a SpliceAI-style delta to PP3/BP4, or route canonical sites to PVS1."""
    cfg = ext.get("splice") or {}
    if not isinstance(signal, dict):
        return None
    if signal.get("canonical_site"):
        return _ext_event("splice", "PVS1", "pathogenic",
                          cfg.get("canonical_site_pvs1_strength"), ext, dict(signal))
    score = signal.get("score")
    if score is None:
        return None
    score = float(score)
    strength = _bin_lookup(score, cfg.get("pp3") or [], descending=True)
    if strength is not None:
        return _ext_event("splice", "PP3", "pathogenic", strength, ext, dict(signal))
    strength = _bin_lookup(score, cfg.get("bp4") or [], descending=False)
    if strength is not None:
        return _ext_event("splice", "BP4", "benign", strength, ext, dict(signal))
    return None


def _cnv_to_event(signal: Any, ext: Dict[str, Any]) -> Optional[EvidenceEvent]:
    """Map a CNV dosage category to its configured criterion + strength."""
    cfg = ext.get("cnv") or {}
    if isinstance(signal, str):
        signal = {"category": signal}
    if not isinstance(signal, dict):
        return None
    rule = cfg.get(str(signal.get("category", "")).strip())
    if not rule:
        return None
    return _ext_event("cnv", rule["criterion"], "pathogenic", rule.get("strength"),
                      ext, dict(signal))


def _noncoding_to_event(signal: Any, ext: Dict[str, Any]) -> Optional[EvidenceEvent]:
    """Map a non-coding / regulatory category to its configured ACMG criterion.

    ``signal`` is a category string or ``{category}`` dict (e.g. ``promoter_established``,
    ``deep_intronic_predicted_splice``, ``noncoding_no_predicted_effect``). Returns
    None for an unknown category, so an unrecognised regulatory annotation is recorded
    as not-applicable rather than scored.
    """
    cfg = ext.get("noncoding") or {}
    if isinstance(signal, str):
        signal = {"category": signal}
    if not isinstance(signal, dict):
        return None
    rule = (cfg.get("category_map") or {}).get(str(signal.get("category", "")).strip())
    if not rule:
        return None
    return _ext_event("noncoding", rule["criterion"], rule.get("direction", "pathogenic"),
                      rule.get("strength"), ext, dict(signal))


def _complex_indel_to_event(signal: Any, ext: Dict[str, Any]) -> Optional[EvidenceEvent]:
    """Map a multi-base / delins indel to PVS1 (frameshift, LoF gene) or PM4 (in-frame).

    ``signal`` is ``{frame: "frameshift"|"inframe", lof_mechanism?, repeat_region?}``.
    A frameshift fires PVS1 only when the gene's mechanism is loss-of-function (PVS1
    must not fire on a gain-of-function gene); an in-frame length-changing indel fires
    PM4 unless it sits in a repeat/low-complexity region (where PM4 does not apply).
    """
    cfg = ext.get("complex_indel") or {}
    if not isinstance(signal, dict):
        return None
    frame = str(signal.get("frame", "")).strip().lower()
    if frame == "frameshift":
        if not signal.get("lof_mechanism"):
            return None
        return _ext_event("complex_indel", "PVS1", "pathogenic",
                          cfg.get("frameshift_lof_strength"), ext, dict(signal))
    if frame == "inframe":
        if signal.get("repeat_region") and not cfg.get("inframe_repeat_region_actionable"):
            return None
        return _ext_event("complex_indel", "PM4", "pathogenic",
                          cfg.get("inframe_nonrepeat_strength"), ext, dict(signal))
    return None


def _mito_to_event(signal: Any, ext: Dict[str, Any]) -> Optional[EvidenceEvent]:
    """Map an mtDNA signal to a criterion using mitochondrial-specific thresholds.

    ``signal`` is ``{af?, heteroplasmy?, het_segregates?}``. Population frequency is
    evaluated first against the (much lower) mtDNA BA1/BS1/PM2 cut-points; when the
    frequency is intermediate or absent, a high heteroplasmy load that segregates with
    phenotype contributes PS4-level evidence. mtDNA thresholds diverge from the
    autosomal model, which is why this is a distinct mapper rather than the gnomAD one.
    """
    cfg = ext.get("mito") or {}
    if not isinstance(signal, dict):
        return None
    af = signal.get("af")
    if af is not None:
        af = float(af)
        if af >= float(cfg.get("ba1_af", 1.0)):
            return _ext_event("mito", "BA1", "benign", "stand_alone", ext, dict(signal))
        if af >= float(cfg.get("bs1_af", 1.0)):
            return _ext_event("mito", "BS1", "benign", "strong", ext, dict(signal))
        if af <= float(cfg.get("pm2_af", 0.0)):
            return _ext_event("mito", "PM2", "pathogenic", "supporting", ext, dict(signal))
        # intermediate frequency -> fall through to heteroplasmy evidence
    het = signal.get("heteroplasmy")
    if (het is not None and signal.get("het_segregates")
            and float(het) >= float(cfg.get("heteroplasmy_min_load", 1.0))):
        return _ext_event("mito", "PS4", "pathogenic",
                          cfg.get("heteroplasmy_segregation_strength"), ext, dict(signal))
    return None


def _repeat_to_event(signal: Any, ext: Dict[str, Any]) -> Optional[EvidenceEvent]:
    """Map a structured repeat count at a known STR locus to a PVS1 expansion call.

    ``signal`` is ``{locus, repeat_count}``. A count at/above the locus pathogenic
    threshold routes to PVS1 (full-penetrance expansion); a count in the reduced-
    penetrance band routes to PVS1 at reduced strength. Normal/intermediate counts and
    unknown loci are non-actionable (None).
    """
    cfg = ext.get("repeat") or {}
    if not isinstance(signal, dict):
        return None
    rule = (cfg.get("loci") or {}).get(str(signal.get("locus", "")).strip().upper())
    if rule is None:
        return None
    raw_count = signal.get("repeat_count")
    if raw_count is None:
        return None
    try:
        count = int(raw_count)
    except (TypeError, ValueError):
        return None
    raw = dict(signal)
    raw["locus_thresholds"] = dict(rule)
    pathogenic_min = rule.get("pathogenic_min")
    if pathogenic_min is not None and count >= int(pathogenic_min):
        return _ext_event("repeat", "PVS1", "pathogenic", cfg.get("pathogenic_strength"), ext, raw)
    rp_max = rule.get("reduced_penetrance_max")
    intermediate_max = rule.get("intermediate_max")
    if (rp_max is not None and intermediate_max is not None
            and int(intermediate_max) < count <= int(rp_max)):
        return _ext_event("repeat", "PVS1", "pathogenic",
                          cfg.get("reduced_penetrance_strength"), ext, raw)
    return None


def _sv_to_event(signal: Any, ext: Dict[str, Any]) -> Optional[EvidenceEvent]:
    """Map a richer structural-variant category to a criterion (breakpoint/dosage aware).

    ``signal`` is a category string or ``{category, gene?}``. Categories tagged
    ``requires_dosage_sensitive`` only fire when no gene is supplied OR the supplied
    gene is in the configured dosage-sensitive list, so a deletion labelled
    "haploinsufficient" but naming a gene with no established dosage sensitivity is
    recorded as not-applicable rather than scored as PVS1.
    """
    cfg = ext.get("sv") or {}
    if isinstance(signal, str):
        signal = {"category": signal}
    if not isinstance(signal, dict):
        return None
    rule = (cfg.get("category_map") or {}).get(str(signal.get("category", "")).strip())
    if not rule:
        return None
    if rule.get("requires_dosage_sensitive") and signal.get("gene") is not None:
        ds = {str(g).upper() for g in (cfg.get("dosage_sensitive_genes") or [])}
        if str(signal.get("gene")).upper() not in ds:
            return None
    return _ext_event("sv", rule["criterion"], rule.get("direction", "pathogenic"),
                      rule.get("strength"), ext, dict(signal))


#: signal key -> mapper, for the extended evidence types.
_EXT_MAPPERS = {
    "pvs1": _pvs1_to_event,
    "functional": _functional_to_event,
    "pm3": _pm3_to_event,
    "segregation": _segregation_to_event,
    "phenotype": _phenotype_to_event,
    "splice": _splice_to_event,
    "cnv": _cnv_to_event,
    "noncoding": _noncoding_to_event,
    "complex_indel": _complex_indel_to_event,
    "mito": _mito_to_event,
    "repeat": _repeat_to_event,
    "sv": _sv_to_event,
}


def derive_extended_criteria(
    signals: Dict[str, Any], ext: Optional[Dict[str, Any]] = None
) -> List[EvidenceEvent]:
    """Turn extended raw signals into standardized EvidenceEvents (job1 task 4).

    Recognized signal keys (each optional; ``None`` is skipped):
      pvs1         consequence str OR {consequence, lof_mechanism, nmd_escape, ...} -> PVS1
      functional   {result, oddspath?, strength?}                                  -> PS3 / BS3
      pm3          {points} OR {observations:[{type}]}                             -> PM3
      segregation  {meioses, segregates}                                          -> PP1 / BS4
      phenotype    specificity str OR {specificity}                               -> PP4
      splice       {score, canonical_site?}                                       -> PP3 / BP4 / PVS1
      cnv          category str OR {category}                                     -> per cnv map
      noncoding    category str OR {category}                                     -> PM1/PM4/PP3/BP4/BP7
      complex_indel {frame, lof_mechanism?, repeat_region?}                       -> PVS1 / PM4
      mito         {af?, heteroplasmy?, het_segregates?}                          -> BA1/BS1/PM2/PS4 (mtDNA)
      repeat       {locus, repeat_count}                                          -> PVS1 (expansion)
      sv           category str OR {category, gene?}                              -> PVS1/PM4/BP4/BA1

    ``ext`` defaults to ``load_coverage_ext()``. The events reuse the engine's
    standard strengths, so ``classify`` sums them with no further changes.
    """
    ext = ext if ext is not None else load_coverage_ext()
    events: List[EvidenceEvent] = []
    for key, mapper in _EXT_MAPPERS.items():
        sig = signals.get(key)
        if sig is None:
            continue
        ev = mapper(sig, ext)
        if ev is not None:
            events.append(ev)
    return events


# --------------------------------------------------------------------------- #
# Computational evidence extension (gap.md A3)                                 #
# --------------------------------------------------------------------------- #
# High-value computational predictors beyond REVEL: AlphaMissense (missense
# PP3/BP4), phyloP conservation (supporting computational), and a documented
# multi-predictor resolution that combines REVEL + AlphaMissense into a SINGLE
# PP3/BP4 (ACMG does not stack in-silico predictors). Thresholds live in the
# SEPARATE ``engine/configs/computational_ext_v1.json`` -- never in ``base_v1.json``
# -- so adding them changes no governed point model and no existing reconstruction
# hash. Gene-level constraint (LOEUF/pLI/missense-Z) is a context modifier and lives
# in ``evidence/computational.py`` because it emits context, not points.

_COMPUTATIONAL_EXT_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "configs", "computational_ext_v1.json"
)
_COMPUTATIONAL_EXT_CACHE: Optional[Dict[str, Any]] = None


def load_computational_ext(path: Optional[str] = None) -> Dict[str, Any]:
    """Load the computational-extension config (cached for the default path)."""
    global _COMPUTATIONAL_EXT_CACHE
    if path is not None:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    if _COMPUTATIONAL_EXT_CACHE is None:
        with open(_COMPUTATIONAL_EXT_PATH, encoding="utf-8") as f:
            _COMPUTATIONAL_EXT_CACHE = json.load(f)
    return _COMPUTATIONAL_EXT_CACHE


def _comp_version(comp: Dict[str, Any]) -> str:
    return "computational_ext_v" + str(comp.get("version", "1.0.0"))


def _stronger_strength(a: Optional[str], b: Optional[str]) -> Optional[str]:
    """Return the stronger of two strengths by ``_STRENGTH_ORDER`` (None-safe)."""
    if a is None:
        return b
    if b is None:
        return a
    if a not in _STRENGTH_ORDER or b not in _STRENGTH_ORDER:
        return a
    return a if _STRENGTH_ORDER.index(a) >= _STRENGTH_ORDER.index(b) else b


def _alphamissense_to_event(
    score: float, comp: "Optional[Dict[str, Any]]" = None
) -> Optional[EvidenceEvent]:
    """Map an AlphaMissense score in [0,1] to a PP3/BP4 event (or None in the band)."""
    comp = comp if comp is not None else load_computational_ext()
    cfg = comp.get("alphamissense") or {}
    strength = _bin_lookup(score, [tuple(b) for b in cfg.get("pp3", [])], descending=True)
    if strength is not None:
        return EvidenceEvent(
            source="alphamissense", acmg_criterion="PP3", evidence_direction="pathogenic",
            applied_strength=strength, source_version=_comp_version(comp),
            raw={"alphamissense_score": score})
    strength = _bin_lookup(score, [tuple(b) for b in cfg.get("bp4", [])], descending=False)
    if strength is not None:
        return EvidenceEvent(
            source="alphamissense", acmg_criterion="BP4", evidence_direction="benign",
            applied_strength=strength, source_version=_comp_version(comp),
            raw={"alphamissense_score": score})
    return None


def _conservation_to_event(
    signal: Any, comp: "Optional[Dict[str, Any]]" = None
) -> Optional[EvidenceEvent]:
    """Map a phyloP conservation score to a supporting PP3/BP4 event (or None)."""
    comp = comp if comp is not None else load_computational_ext()
    cfg = comp.get("conservation") or {}
    phylop = signal.get("phylop") if isinstance(signal, dict) else signal
    if phylop is None:
        return None
    phylop = float(phylop)
    strength = _bin_lookup(phylop, [tuple(b) for b in cfg.get("phylop_pp3", [])], descending=True)
    if strength is not None:
        return EvidenceEvent(
            source="conservation", acmg_criterion="PP3", evidence_direction="pathogenic",
            applied_strength=strength, source_version=_comp_version(comp),
            raw={"phylop": phylop})
    strength = _bin_lookup(phylop, [tuple(b) for b in cfg.get("phylop_bp4", [])], descending=False)
    if strength is not None:
        return EvidenceEvent(
            source="conservation", acmg_criterion="BP4", evidence_direction="benign",
            applied_strength=strength, source_version=_comp_version(comp),
            raw={"phylop": phylop})
    return None


def resolve_missense_consensus(
    revel: Optional[float] = None,
    alphamissense: Optional[float] = None,
    *,
    config: "Optional[Any]" = None,
    comp: "Optional[Dict[str, Any]]" = None,
) -> Optional[EvidenceEvent]:
    """Combine REVEL + AlphaMissense into ONE PP3/BP4 event (gap.md A3 calibration).

    ACMG/AMP guidance is that multiple in-silico missense predictors contribute a
    single PP3/BP4, not one each. With only one predictor present this returns that
    predictor's event unchanged (so a REVEL-only signal is byte-identical to the
    legacy behavior). When both are present and **agree** on direction, one event is
    emitted at the stronger of the two strengths (``agreement_combine``). When they
    **disagree** (one pathogenic, one benign) the ``disagreement_policy`` decides:
    ``conservative`` (default) emits nothing -- a documented no-call, never a silent
    average -- while ``revel`` / ``alphamissense`` trust the named predictor.
    """
    comp = comp if comp is not None else load_computational_ext()
    revel_ev = _revel_to_event(float(revel), config) if revel is not None else None
    am_ev = _alphamissense_to_event(float(alphamissense), comp) if alphamissense is not None else None

    if revel_ev is None and am_ev is None:
        return None
    if am_ev is None:
        return revel_ev          # REVEL-only: identical to the legacy single-REVEL path
    if revel_ev is None:
        return am_ev             # AlphaMissense-only

    # Both predictors fired, so both scores are present (narrow Optional -> float).
    r = float(revel) if revel is not None else 0.0
    a = float(alphamissense) if alphamissense is not None else 0.0

    cfg = comp.get("missense_consensus") or {}
    if revel_ev.evidence_direction == am_ev.evidence_direction:
        combine = cfg.get("agreement_combine", "stronger")
        strength = (_stronger_strength(revel_ev.applied_strength, am_ev.applied_strength)
                    if combine == "stronger" else revel_ev.applied_strength)
        return EvidenceEvent(
            source="revel+alphamissense",
            acmg_criterion=revel_ev.acmg_criterion,     # both PP3 or both BP4
            evidence_direction=revel_ev.evidence_direction,
            applied_strength=strength,
            source_version="REVEL+" + _comp_version(comp),
            raw={"revel_score": r, "alphamissense_score": a,
                 "agreement": True, "policy": "agreement_" + combine})

    policy = cfg.get("disagreement_policy", "conservative")
    if policy == "revel":
        chosen = revel_ev
    elif policy == "alphamissense":
        chosen = am_ev
    else:
        return None              # conservative: predictors conflict -> no call
    return EvidenceEvent(
        source="missense_consensus", acmg_criterion=chosen.acmg_criterion,
        evidence_direction=chosen.evidence_direction, applied_strength=chosen.applied_strength,
        source_version="REVEL+" + _comp_version(comp),
        raw={"revel_score": r, "alphamissense_score": a,
             "agreement": False, "policy": policy, "chosen": chosen.source})
