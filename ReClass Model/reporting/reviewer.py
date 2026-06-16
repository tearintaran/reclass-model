"""Technical reviewer report builder.

Assembles everything a credentialed reviewer needs to audit *why* a tier was
produced before signing it off: canonical identity, evidence grouped by source,
a per-criterion contribution table (criterion, strength, direction, points,
provenance, warning status, source records), the engine version + reconstruction
hash, and the full history — prior classifications, reanalysis events, and
tier-crossing alerts. Same-tier reanalysis changes are surfaced as audit history,
distinct from tier-crossing alerts, matching the "no high-priority paging for
same-tier changes" rule.

Pure: it takes plain receipt/event dicts and returns a plain dict.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .common import (
    DEFAULT_LIMITATIONS,
    bundle_to_dict,
    identity_block,
    now_iso,
    release_status,
)


def _events_by_source(events: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for ev in events:
        grouped.setdefault(ev.get("source", "unknown"), []).append(ev)
    return grouped


def _source_warnings(source: str, warnings: List[str]) -> List[str]:
    """Warnings attributable to a source (resolver tags them ``source:reason``)."""
    out = []
    for w in warnings:
        if w == source or w.startswith(f"{source}:"):
            out.append(w)
    return out


def _criteria_rows(
    contributions: List[Dict[str, Any]],
    events: List[Dict[str, Any]],
    warnings: List[str],
) -> List[Dict[str, Any]]:
    """Join receipt contributions with bundle events for provenance + warnings."""
    rows: List[Dict[str, Any]] = []
    for c in contributions:
        provenance: Optional[Dict[str, Any]] = None
        for ev in events:
            if (
                ev.get("source") == c.get("source")
                and ev.get("acmg_criterion") == c.get("acmg_criterion")
                and ev.get("evidence_direction") == c.get("evidence_direction")
            ):
                provenance = ev.get("raw") or {}
                break
        rows.append({
            "criterion": c.get("acmg_criterion"),
            "direction": c.get("evidence_direction"),
            "strength": c.get("applied_strength"),
            "points": c.get("points"),
            "source": c.get("source"),
            "source_version": c.get("source_version"),
            "provenance": provenance,
            "warnings": _source_warnings(c.get("source", ""), warnings),
        })
    return rows


def build_reviewer_report(
    *,
    classification: Dict[str, Any],
    evidence_bundle: Any = None,
    prior_classifications: Optional[List[Dict[str, Any]]] = None,
    reanalysis_events: Optional[List[Dict[str, Any]]] = None,
    alerts: Optional[List[Dict[str, Any]]] = None,
    normalized_identity: Optional[Dict[str, Any]] = None,
    limitations: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Build the structured technical reviewer report (see module docstring)."""
    bundle = bundle_to_dict(evidence_bundle) or {}
    events = bundle.get("events", []) or []
    warnings = list(bundle.get("warnings", []) or [])
    contributions = classification.get("contributions", []) or []

    prior = list(prior_classifications or [])
    # Don't list the receipt under review as its own "previous" classification.
    this_id = classification.get("classification_id")
    previous = [p for p in prior if p.get("classification_id") != this_id]

    rean = list(reanalysis_events or [])
    same_tier = [e for e in rean if not e.get("crossed")]
    crossings = [e for e in rean if e.get("crossed")]

    return {
        "report_type": "technical_reviewer",
        "generated_utc": now_iso(),
        "release_status": release_status(classification),
        "identity": identity_block(classification, normalized_identity),
        "classification": {
            "classification_id": this_id,
            "tier": classification.get("tier"),
            "total_points": classification.get("total_points"),
            "engine_version": classification.get("engine_version"),
            "reconstruction_hash": classification.get("reconstruction_hash"),
            "overrides": classification.get("overrides", []),
        },
        "evidence_by_source": _events_by_source(events),
        "criteria": _criteria_rows(contributions, events, warnings),
        "evidence_provenance": {
            "provider_versions": bundle.get("provider_versions", {}),
            "source_records": bundle.get("source_records", []),
            "match": bundle.get("match"),
            "warnings": warnings,
        },
        "history": {
            "previous_classifications": previous,
            "reanalysis_events": rean,
            "alerts": list(alerts or []),
        },
        "audit": {
            "same_tier_changes": same_tier,
            "tier_crossings": crossings,
            "note": "Same-tier evidence changes are audit history only and do not "
                    "raise high-priority clinical alerts; only tier crossings do.",
        },
        "source_versions": dict(bundle.get("provider_versions", {})),
        "warnings": warnings,
        "limitations": list(limitations) if limitations is not None else list(DEFAULT_LIMITATIONS),
    }
