"""Evidence model layer: a serializable, provenance-rich bundle of evidence.

`EvidenceBundle` is the unit a provider returns. It carries the standardized
`engine.scoring.EvidenceEvent` list the engine will sum, plus everything a human
reviewer (or a re-analysis run) needs to audit *where each point came from*:

  * `provider_versions` -- which source/provider version produced the events,
  * `source_records`    -- the raw matched records the events were derived from,
  * `warnings`          -- deterministic data-quality flags (e.g. label disagreement),
  * `match`             -- how identity resolution joined this bundle to a source,
  * `transcript`        -- the MANE Select / RefSeq transcript identity the evidence
                           was interpreted against (job1 task 4), carried so a
                           reviewer/API/report can name the exact transcript,
  * `cohort_counts`     -- PS4 denominator and case/control cohort counts (job1 task
                           5), modeled here so a stored bundle preserves the cohort a
                           case-control PS4 point was derived from.

The bundle round-trips through JSON without losing events or provenance (gap.md
Phase 1B), so a stored bundle can recreate the same `EvidenceEvent` list and the
same engine `reconstruction_hash`. The `transcript` and `cohort_counts` fields are
additive and optional (default ``None``): a bundle written without them deserializes
identically, and the reconstruction hash -- computed only over the events -- is
unaffected, so adding identity/cohort provenance never changes a classification.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from engine.scoring import EvidenceEvent
from engine.scoring import reconstruction_hash as _reconstruction_hash
from engine import config as _C

# Additive, backward-compatible field additions (transcript identity + PS4 cohort
# counts, job1 tasks 4-5) keep this at 1.0.0: old readers ignore the new keys, and a
# bundle serialized without them deserializes unchanged. Bump only on an
# *incompatible* shape change.
SCHEMA_VERSION = "1.0.0"

# Field order used for a stable EvidenceEvent dict (matches the dataclass).
_EVENT_FIELDS = (
    "source",
    "acmg_criterion",
    "evidence_direction",
    "applied_strength",
    "points",
    "source_version",
    "raw",
)


def event_to_dict(event: EvidenceEvent) -> Dict[str, Any]:
    """Serialize an EvidenceEvent to a plain, stable-key dict."""
    return {
        "source": event.source,
        "acmg_criterion": event.acmg_criterion,
        "evidence_direction": event.evidence_direction,
        "applied_strength": event.applied_strength,
        "points": event.points,
        "source_version": event.source_version,
        "raw": dict(event.raw),
    }


def event_from_dict(d: Dict[str, Any]) -> EvidenceEvent:
    """Rebuild an EvidenceEvent from a dict produced by :func:`event_to_dict`."""
    return EvidenceEvent(
        source=d["source"],
        acmg_criterion=d["acmg_criterion"],
        evidence_direction=d["evidence_direction"],
        applied_strength=d.get("applied_strength"),
        points=d.get("points"),
        source_version=d.get("source_version"),
        raw=dict(d.get("raw") or {}),
    )


@dataclass(frozen=True)
class TranscriptIdentity:
    """MANE Select / RefSeq transcript identity for one interpreted variant (task 4).

    Transcript-dependent evidence (PVS1 LoF consequence, splice, coding HGVS) is only
    interpretable *against a named transcript*. This carries that identity into the
    evidence bundle so a downstream consumer (API -- Job 3; reviewer/clinical report
    -- Jobs 2/3) can show exactly which transcript was used. ``mane_select`` is the
    MANE Select RefSeq transcript (e.g. ``NM_000277.3``); ``mane_plus_clinical`` an
    optional MANE Plus Clinical transcript; the HGVS fields are the coding/protein
    expressions on that transcript. Every field is optional -- absence is recorded,
    never guessed.
    """

    mane_select: Optional[str] = None
    mane_plus_clinical: Optional[str] = None
    refseq: Optional[str] = None
    ensembl: Optional[str] = None
    gene: Optional[str] = None
    hgvs_c: Optional[str] = None
    hgvs_p: Optional[str] = None
    source: Optional[str] = None

    @property
    def is_mane_select(self) -> bool:
        """True when a MANE Select transcript identity is present."""
        return bool(self.mane_select)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> "Optional[TranscriptIdentity]":
        if not d:
            return None
        fields = {
            "mane_select", "mane_plus_clinical", "refseq", "ensembl",
            "gene", "hgvs_c", "hgvs_p", "source",
        }
        return cls(**{k: d.get(k) for k in fields})

    @classmethod
    def from_case(cls, case: Any) -> "Optional[TranscriptIdentity]":
        """Extract a transcript identity from a fixture case (job1 task 4).

        Prefers a structured ``case['transcript']`` block; falls back to transcript
        fields under ``case['provenance']`` (``mane_transcript`` / ``refseq_transcript``
        / ``hgvs_c`` / ``hgvs_p``). The case ``gene`` fills in a missing gene. Returns
        ``None`` when no transcript identity is present (never invented).
        """
        if not isinstance(case, dict):
            return None
        tx = case.get("transcript")
        if isinstance(tx, dict) and any(tx.get(k) for k in
                                        ("mane_select", "mane_plus_clinical", "refseq",
                                         "ensembl", "hgvs_c", "hgvs_p")):
            merged = dict(tx)
            merged.setdefault("gene", case.get("gene"))
            return cls.from_dict(merged)
        prov = case.get("provenance") or {}
        transcript = prov.get("mane_transcript") or prov.get("refseq_transcript")
        hgvs_c = prov.get("hgvs_c")
        hgvs_p = prov.get("hgvs_p")
        if transcript or hgvs_c or hgvs_p:
            return cls(
                mane_select=prov.get("mane_transcript"),
                refseq=prov.get("refseq_transcript"),
                gene=case.get("gene"), hgvs_c=hgvs_c, hgvs_p=hgvs_p,
                source=prov.get("source"),
            )
        return None


@dataclass(frozen=True)
class CohortCounts:
    """PS4 denominator + case/control cohort counts for one variant (job1 task 5).

    PS4 ("the prevalence of the variant in affected individuals is significantly
    increased compared with controls") is only auditable with the underlying cohort:
    how many cases and controls carried the allele, the denominators it was observed
    against, and the resulting effect size. This models that record in the
    evidence/ingest layer; transporting it through storage/reanalysis/alerting is Job
    3 and surfacing it in reviewer reports is Job 2 -- this layer stops at producing
    the populated field. Counts are integers (``None`` when unknown -- never imputed);
    ``odds_ratio`` / ``ci_low`` / ``ci_high`` / ``p_value`` carry the computed effect.
    """

    case_count: Optional[int] = None
    case_total: Optional[int] = None
    control_count: Optional[int] = None
    control_total: Optional[int] = None
    odds_ratio: Optional[float] = None
    ci_low: Optional[float] = None
    ci_high: Optional[float] = None
    p_value: Optional[float] = None
    cohort: Optional[str] = None
    source: Optional[str] = None

    @property
    def denominator(self) -> Optional[int]:
        """Total cohort denominator (cases + controls) when both totals are known."""
        if self.case_total is None or self.control_total is None:
            return None
        return int(self.case_total) + int(self.control_total)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["denominator"] = self.denominator
        return d

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> "Optional[CohortCounts]":
        if not d:
            return None
        fields = {
            "case_count", "case_total", "control_count", "control_total",
            "odds_ratio", "ci_low", "ci_high", "p_value", "cohort", "source",
        }
        return cls(**{k: d.get(k) for k in fields})


@dataclass
class EvidenceBundle:
    """Provenance-rich container of standardized evidence for one variant/case."""

    variant_key: Optional[str] = None
    events: List[EvidenceEvent] = field(default_factory=list)
    provider_versions: Dict[str, str] = field(default_factory=dict)
    source_records: List[Dict[str, Any]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    match: Optional[Dict[str, Any]] = None
    #: MANE Select / RefSeq transcript identity the evidence was interpreted against
    #: (job1 task 4). Optional; ``None`` when no transcript context is available.
    transcript: Optional[TranscriptIdentity] = None
    #: PS4 denominator + case/control cohort counts (job1 task 5). Optional; ``None``
    #: when the bundle carries no case-control evidence.
    cohort_counts: Optional[CohortCounts] = None

    # -- derived helpers ---------------------------------------------------- #
    def reconstruction_hash(self, engine_version: str = _C.ENGINE_VERSION) -> str:
        """Engine reconstruction hash over this bundle's events.

        Two bundles with the same events produce the same hash, so a saved bundle
        verifies byte-for-byte against a re-derived classification.
        """
        return _reconstruction_hash(self.events, engine_version)

    # -- serialization ------------------------------------------------------ #
    def to_dict(self) -> Dict[str, Any]:
        """Stable, JSON-ready dict (events fully serialized with provenance)."""
        return {
            "schema_version": SCHEMA_VERSION,
            "variant_key": self.variant_key,
            "events": [event_to_dict(e) for e in self.events],
            "provider_versions": dict(self.provider_versions),
            "source_records": [dict(r) for r in self.source_records],
            "warnings": list(self.warnings),
            "match": dict(self.match) if self.match is not None else None,
            "transcript": self.transcript.to_dict() if self.transcript is not None else None,
            "cohort_counts": (
                self.cohort_counts.to_dict() if self.cohort_counts is not None else None
            ),
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "EvidenceBundle":
        """Inverse of :meth:`to_dict`; recreates the same EvidenceEvent list.

        Tolerates dicts written by an older serializer (no ``transcript`` /
        ``cohort_counts`` keys): the additive fields simply default to ``None``.
        """
        match = d.get("match")
        return cls(
            variant_key=d.get("variant_key"),
            events=[event_from_dict(e) for e in d.get("events", [])],
            provider_versions=dict(d.get("provider_versions") or {}),
            source_records=[dict(r) for r in d.get("source_records") or []],
            warnings=list(d.get("warnings") or []),
            match=dict(match) if match is not None else None,
            transcript=TranscriptIdentity.from_dict(d.get("transcript")),
            cohort_counts=CohortCounts.from_dict(d.get("cohort_counts")),
        )

    def to_json(self, *, indent: Optional[int] = None) -> str:
        """Canonical JSON string (sorted keys for stable, diff-friendly output)."""
        return json.dumps(self.to_dict(), sort_keys=True, indent=indent)

    @classmethod
    def from_json(cls, text: str) -> "EvidenceBundle":
        return cls.from_dict(json.loads(text))
