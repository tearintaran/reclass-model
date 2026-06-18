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
    stratification_block,
)


def _first_present(*values: Any) -> Any:
    for value in values:
        if value not in (None, ""):
            return value
    return None


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


def _criterion_name(row: Dict[str, Any]) -> Any:
    return _first_present(row.get("acmg_criterion"), row.get("criterion"))


def _criterion_direction(row: Dict[str, Any]) -> Any:
    direction = _first_present(row.get("evidence_direction"), row.get("direction"))
    if direction:
        return direction
    name = str(_criterion_name(row) or "").upper()
    if not name:
        return None
    if name[0] == "P":
        return "pathogenic"
    if name[0] == "B":
        return "benign"
    return None


def _case_criteria(case: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not case:
        return []
    signals = case.get("signals", {}) or {}
    return [dict(row) for row in signals.get("criteria", []) or []]


def _evidence_extensions(case: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Surface optional Job-1 evidence fields when fixtures already carry them."""
    case = case or {}
    signals = case.get("signals", {}) or {}
    ps4 = _first_present(
        case.get("ps4_cohort_counts"),
        case.get("cohort_counts"),
        signals.get("ps4_cohort_counts"),
        signals.get("cohort_counts"),
        signals.get("ps4"),
    )
    mane = _first_present(
        case.get("mane_select_transcript"),
        case.get("mane_transcript"),
        case.get("transcript"),
        signals.get("mane_select_transcript"),
        signals.get("mane_transcript"),
        signals.get("transcript"),
    )
    return {
        "ps4_cohort_counts": ps4,
        "mane_transcript": mane,
    }


def _decision_block(review_decision: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    block = {
        "status": "pending",
        "decision": None,
        "reviewer_disposition": None,
        "rationale": None,
        "decided_by": None,
        "decided_at": None,
    }
    if review_decision:
        block.update(review_decision)
    if block.get("reviewer_disposition") is None and block.get("decision") is not None:
        block["reviewer_disposition"] = block["decision"]
    return block


def _proposal_block(
    *,
    case_id: Any,
    proposed_remediation: Optional[str],
    override_proposal: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    proposed = bool(proposed_remediation or override_proposal)
    block = {
        "proposal_id": f"{case_id}:variant_specific_exception" if case_id else None,
        "proposed": proposed,
        "status": "pending" if proposed else "not_proposed",
        "scope": "variant_specific",
        "proposed_change": proposed_remediation,
        "accepted": False,
        "rejected": False,
        "global_threshold_change": False,
    }
    if override_proposal:
        block.update(override_proposal)
    status = str(block.get("status") or "").lower()
    block["accepted"] = bool(block.get("accepted")) or status == "accepted"
    block["rejected"] = bool(block.get("rejected")) or status == "rejected"
    block["global_threshold_change"] = bool(block.get("global_threshold_change", False))
    return block


def _sign_off_block(sign_off: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    block = {
        "required": True,
        "signed": False,
        "signed_off_by": None,
        "signed_off_at": None,
        "signature_id": None,
    }
    if sign_off:
        block.update(sign_off)
    block["signed"] = bool(block.get("signed") or block.get("signed_off_by"))
    return block


def build_validation_review_packet(
    *,
    case: Dict[str, Any],
    result: Optional[Dict[str, Any]] = None,
    benchmark: Optional[str] = None,
    classification: Optional[Dict[str, Any]] = None,
    root_cause_category: Optional[str] = None,
    proposed_remediation: Optional[str] = None,
    review_decision: Optional[Dict[str, Any]] = None,
    override_proposal: Optional[Dict[str, Any]] = None,
    sign_off: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build a JSON-native per-case validation review packet.

    The packet is intentionally workflow-shaped: it has explicit reviewer
    decision, override-proposal, and sign-off blocks that can be round-tripped
    through JSON before any credentialed reviewer fills them in.
    """
    result = result or {}
    classification = classification or {}
    case_id = _first_present(case.get("id"), result.get("id"))
    criteria = _case_criteria(case)
    extensions = _evidence_extensions(case)
    return {
        "packet_type": "validation_case_review_packet",
        "schema_version": "1.0.0",
        "generated_utc": now_iso(),
        "benchmark": benchmark or case.get("benchmark"),
        "case": {
            "id": case_id,
            "gene": _first_present(case.get("gene"), result.get("gene")),
            "variant_key": _first_present(case.get("variant_key"), classification.get("variant_key")),
            "disease": case.get("disease"),
            "population": case.get("population"),
            "vcep_group": case.get("vcep_group"),
            "variant_class": _first_present(case.get("variant_class"), case.get("variant_type")),
            "expected_tier": _first_present(case.get("expected"), result.get("expected")),
            "predicted_tier": _first_present(result.get("predicted"), classification.get("tier")),
            "points": _first_present(result.get("points"), classification.get("total_points")),
            "serious_discordance": bool(result.get("serious")),
        },
        "classification": {
            "tier": _first_present(classification.get("tier"), result.get("predicted")),
            "total_points": _first_present(classification.get("total_points"), result.get("points")),
            "engine_version": classification.get("engine_version"),
            "reconstruction_hash": classification.get("reconstruction_hash"),
            "overrides": list(classification.get("overrides", []) or []),
        },
        "evidence_summary": {
            "criteria": [
                {
                    "criterion": _criterion_name(row),
                    "direction": _criterion_direction(row),
                    "strength": _first_present(row.get("applied_strength"), row.get("strength")),
                    "source": row.get("source"),
                    "source_version": _first_present(row.get("source_version"), row.get("version")),
                }
                for row in criteria
            ],
            "ps4_cohort_counts": extensions["ps4_cohort_counts"],
            "mane_transcript": extensions["mane_transcript"],
        },
        "adjudication": {
            "root_cause_category": root_cause_category,
            "proposed_remediation": proposed_remediation,
        },
        "reviewer_decision": _decision_block(review_decision),
        "override_proposal": _proposal_block(
            case_id=case_id,
            proposed_remediation=proposed_remediation,
            override_proposal=override_proposal,
        ),
        "sign_off": _sign_off_block(sign_off),
        "machine_readable": True,
    }


def build_reviewer_report(
    *,
    classification: Dict[str, Any],
    evidence_bundle: Any = None,
    prior_classifications: Optional[List[Dict[str, Any]]] = None,
    reanalysis_events: Optional[List[Dict[str, Any]]] = None,
    alerts: Optional[List[Dict[str, Any]]] = None,
    normalized_identity: Optional[Dict[str, Any]] = None,
    stratification: Optional[Dict[str, Any]] = None,
    limitations: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Build the structured technical reviewer report (see module docstring).

    ``stratification`` (when given) carries the case's population/VCEP fields; it
    defaults to reading them off ``classification``. They are reported as DISTINCT
    families (true ancestry vs expert-panel grouping) so the two are never conflated.
    """
    bundle = bundle_to_dict(evidence_bundle) or {}
    warnings = list(bundle.get("warnings", []) or [])
    contributions = classification.get("contributions", []) or []
    events = bundle.get("events", []) or []
    if not events and contributions:
        events = [dict(c) for c in contributions]

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
        "stratification": stratification_block(stratification or classification),
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
        # Optional Job-1 evidence-model fields carried on the resolved bundle: the
        # MANE Select transcript identity (task 4) the evidence was interpreted
        # against and the PS4 denominator + case/control cohort counts (task 5).
        # Both are None when the bundle carried no such context.
        "evidence_extensions": {
            "transcript": bundle.get("transcript"),
            "cohort_counts": bundle.get("cohort_counts"),
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
