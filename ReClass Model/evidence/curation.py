"""Curation-queue detection (job1 task 3).

Some evidence problems are not "missing data" but "data we cannot safely use yet":
an identity that did not match (or matched ambiguously), a transcript-dependent
criterion asserted without a named transcript, a ``PS4`` claim with no cohort
denominator, or a variant carrying both pathogenic and benign evidence. These need
a *human* decision, so the workbench surfaces them as curation work items.

This module only **surfaces** them — it inspects a resolved
:class:`~evidence.model.EvidenceBundle` (or its ``to_dict()`` form) and returns the
:class:`CurationItem` list a reviewer should triage. The *resolution policy* (how a
conflict is adjudicated, how an ambiguous identity is chosen) lives in Job 2; this
job stops at detection. Detection is pure and deterministic: the same bundle always
yields the same items, in a stable order.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

#: The curation item kinds (mirrors the ``clinical.curation_queue.kind`` CHECK).
CURATION_KINDS = (
    "unmatched_identity",
    "ambiguous_identity",
    "missing_transcript",
    "missing_cohort_denominator",
    "pathogenic_benign_conflict",
)

#: Criteria whose interpretation depends on a named transcript (consequence / coding
#: position). Asserting one of these without a transcript identity is a curation gap.
TRANSCRIPT_DEPENDENT_CRITERIA = {"PVS1", "PS1", "PM5", "PM4", "BP3", "BP7"}

_SEVERITY = ("info", "warning", "blocker")


@dataclass
class CurationItem:
    """One surfaced curation gap. ``detail`` carries the evidence behind it."""

    kind: str
    variant_key: Optional[str] = None
    severity: str = "warning"
    detail: Optional[Dict[str, Any]] = None

    def __post_init__(self) -> None:
        if self.kind not in CURATION_KINDS:
            raise ValueError(f"curation kind must be one of {CURATION_KINDS}, got {self.kind!r}")
        if self.severity not in _SEVERITY:
            raise ValueError(f"severity must be one of {_SEVERITY}, got {self.severity!r}")
        if self.detail is None:
            self.detail = {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "variant_key": self.variant_key,
            "severity": self.severity,
            "detail": dict(self.detail or {}),
        }


def _as_dict(bundle: Any) -> Dict[str, Any]:
    """Accept an EvidenceBundle (or its dict form) and return the dict view."""
    if hasattr(bundle, "to_dict"):
        return bundle.to_dict()
    if isinstance(bundle, dict):
        return dict(bundle)
    raise TypeError("scan_bundle expects an EvidenceBundle or its to_dict() form")


def _criteria(events: List[Dict[str, Any]]) -> List[str]:
    return [str(e.get("acmg_criterion", "")).upper() for e in events]


def _directions(events: List[Dict[str, Any]]) -> List[str]:
    return [str(e.get("evidence_direction", "")).lower() for e in events]


def _match_blocks(match: Any) -> List[Dict[str, Any]]:
    """Normalize the bundle ``match`` (a dict of provider->block, or a single block).

    The merged resolver shape is ``{provider_name: block_or_None}`` (values are dicts
    or None); a single adapter block has scalar values (status / called / keys). An
    empty/absent match yields no blocks (so an unqueried bundle is never treated as a
    failed match).
    """
    if not isinstance(match, dict) or not match:
        return []
    values = list(match.values())
    if any(isinstance(v, dict) for v in values) and all(
        isinstance(v, (dict, type(None))) for v in values
    ):
        return [v for v in values if isinstance(v, dict)]
    return [match]


def detect_unmatched_identity(bundle: Dict[str, Any]) -> List[CurationItem]:
    """Surface an identity that no source matched (an honest miss, never a guess)."""
    events = bundle.get("events") or []
    if events:
        return []  # something matched and produced evidence
    blocks = _match_blocks(bundle.get("match"))
    queried = bool(blocks) or any(
        not w.endswith("no_providers_configured") for w in bundle.get("warnings") or []
    )
    if not queried:
        return []
    # No events from any provider that was actually queried => unmatched identity.
    any_called = any(
        b.get("called") is True or b.get("matched") is True or b.get("status") == "called"
        for b in blocks
    )
    if any_called:
        return []
    return [CurationItem(
        kind="unmatched_identity",
        variant_key=bundle.get("variant_key"),
        severity="blocker",
        detail={
            "warnings": list(bundle.get("warnings") or []),
            "providers": sorted(bundle.get("provider_versions") or {}),
        },
    )]


def detect_ambiguous_identity(bundle: Dict[str, Any]) -> List[CurationItem]:
    """Surface an identity that matched more than one candidate source record."""
    items: List[CurationItem] = []
    for block in _match_blocks(bundle.get("match")):
        candidates = block.get("candidates")
        ambiguous = block.get("ambiguous") is True or block.get("status") == "ambiguous"
        if ambiguous or (isinstance(candidates, list) and len(candidates) > 1):
            items.append(CurationItem(
                kind="ambiguous_identity",
                variant_key=bundle.get("variant_key"),
                severity="warning",
                detail={"match": block},
            ))
    # Warning-driven signal (providers may flag ambiguity textually).
    if not items:
        amb = [w for w in bundle.get("warnings") or [] if "ambiguous" in str(w).lower()]
        if amb:
            items.append(CurationItem(
                kind="ambiguous_identity",
                variant_key=bundle.get("variant_key"),
                severity="warning",
                detail={"warnings": amb},
            ))
    return items


def detect_missing_transcript(bundle: Dict[str, Any]) -> List[CurationItem]:
    """Surface a transcript-dependent criterion asserted without a transcript."""
    if bundle.get("transcript"):
        return []
    flagged = sorted({
        c for c in _criteria(bundle.get("events") or [])
        if c in TRANSCRIPT_DEPENDENT_CRITERIA
    })
    if not flagged:
        return []
    return [CurationItem(
        kind="missing_transcript",
        variant_key=bundle.get("variant_key"),
        severity="warning",
        detail={"criteria": flagged},
    )]


def detect_missing_cohort_denominator(bundle: Dict[str, Any]) -> List[CurationItem]:
    """Surface a PS4 claim whose cohort denominator is unknown (unauditable)."""
    if "PS4" not in _criteria(bundle.get("events") or []):
        return []
    cohort = bundle.get("cohort_counts")
    if isinstance(cohort, dict) and cohort.get("denominator") is not None:
        return []
    return [CurationItem(
        kind="missing_cohort_denominator",
        variant_key=bundle.get("variant_key"),
        severity="blocker",
        detail={"cohort_counts": cohort},
    )]


def detect_pathogenic_benign_conflict(bundle: Dict[str, Any]) -> List[CurationItem]:
    """Surface a variant carrying both pathogenic and benign evidence."""
    directions = set(_directions(bundle.get("events") or []))
    if "pathogenic" in directions and "benign" in directions:
        events = bundle.get("events") or []
        return [CurationItem(
            kind="pathogenic_benign_conflict",
            variant_key=bundle.get("variant_key"),
            severity="blocker",
            detail={
                "pathogenic": sorted({
                    str(e.get("acmg_criterion", "")).upper() for e in events
                    if str(e.get("evidence_direction", "")).lower() == "pathogenic"
                }),
                "benign": sorted({
                    str(e.get("acmg_criterion", "")).upper() for e in events
                    if str(e.get("evidence_direction", "")).lower() == "benign"
                }),
            },
        )]
    return []


_DETECTORS = (
    detect_unmatched_identity,
    detect_ambiguous_identity,
    detect_missing_transcript,
    detect_missing_cohort_denominator,
    detect_pathogenic_benign_conflict,
)


def scan_bundle(bundle: Any) -> List[CurationItem]:
    """Run every detector over a resolved bundle; return the surfaced items.

    Items are returned in a stable detector order (identity, transcript, cohort,
    conflict) so the queue is deterministic for a fixed bundle.
    """
    view = _as_dict(bundle)
    items: List[CurationItem] = []
    for detector in _DETECTORS:
        items.extend(detector(view))
    return items
