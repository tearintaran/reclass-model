"""Evidence model layer: a serializable, provenance-rich bundle of evidence.

`EvidenceBundle` is the unit a provider returns. It carries the standardized
`engine.scoring.EvidenceEvent` list the engine will sum, plus everything a human
reviewer (or a re-analysis run) needs to audit *where each point came from*:

  * `provider_versions` -- which source/provider version produced the events,
  * `source_records`    -- the raw matched records the events were derived from,
  * `warnings`          -- deterministic data-quality flags (e.g. label disagreement),
  * `match`             -- how identity resolution joined this bundle to a source.

The bundle round-trips through JSON without losing events or provenance (gap.md
Phase 1B), so a stored bundle can recreate the same `EvidenceEvent` list and the
same engine `reconstruction_hash`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from engine.scoring import EvidenceEvent
from engine.scoring import reconstruction_hash as _reconstruction_hash
from engine import config as _C

# Bump if the serialized bundle shape changes incompatibly.
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


@dataclass
class EvidenceBundle:
    """Provenance-rich container of standardized evidence for one variant/case."""

    variant_key: Optional[str] = None
    events: List[EvidenceEvent] = field(default_factory=list)
    provider_versions: Dict[str, str] = field(default_factory=dict)
    source_records: List[Dict[str, Any]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    match: Optional[Dict[str, Any]] = None

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
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "EvidenceBundle":
        """Inverse of :meth:`to_dict`; recreates the same EvidenceEvent list."""
        match = d.get("match")
        return cls(
            variant_key=d.get("variant_key"),
            events=[event_from_dict(e) for e in d.get("events", [])],
            provider_versions=dict(d.get("provider_versions") or {}),
            source_records=[dict(r) for r in d.get("source_records") or []],
            warnings=list(d.get("warnings") or []),
            match=dict(match) if match is not None else None,
        )

    def to_json(self, *, indent: Optional[int] = None) -> str:
        """Canonical JSON string (sorted keys for stable, diff-friendly output)."""
        return json.dumps(self.to_dict(), sort_keys=True, indent=indent)

    @classmethod
    def from_json(cls, text: str) -> "EvidenceBundle":
        return cls.from_dict(json.loads(text))
