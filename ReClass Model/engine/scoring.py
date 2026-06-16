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
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

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
# Classification                                                              #
# --------------------------------------------------------------------------- #
def classify(
    evidence: List[EvidenceEvent],
    engine_version: Optional[str] = None,
    config: "Optional[Any]" = None,
) -> Classification:
    """Sum standardized evidence into an ACMG/AMP tier. Pure and deterministic.

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

    # Stand-alone benign rule: BA1 alone classifies Benign (ACMG/AMP 2015).
    has_ba1 = any(
        e.acmg_criterion.upper() == "BA1" and e.evidence_direction == "benign"
        for e in evidence
    )

    total = 0.0
    for e in evidence:
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
      revel            float REVEL score              -> PP3 / BP4
      gnomad_af        float popmax allele frequency  -> BA1 / BS1 / PM2
      criteria         list of {criterion, direction, strength[, source, version]}
                       pre-mapped expert/curated criteria (PVS1, PS1, PM1, ...)

    ``config`` (optional) is a versioned ``EngineConfig`` whose REVEL bins and
    allele-frequency cutoffs are used instead of the base config's -- used by
    threshold-sensitivity analysis. When omitted, behavior is byte-identical to
    the base config.
    """
    events: List[EvidenceEvent] = []

    if "revel" in signals and signals["revel"] is not None:
        ev = _revel_to_event(float(signals["revel"]), config)
        if ev:
            events.append(ev)

    if "gnomad_af" in signals and signals["gnomad_af"] is not None:
        ev = _gnomad_af_to_event(float(signals["gnomad_af"]), config)
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
