"""Release-gate evaluation for clinical sign-off packets."""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Mapping, Optional

from validation.conflict_policy import disposition_blocks_release, normalize_disposition
from validation.signoff import (
    APPROVED_FOR_RELEASE,
    RELEASE_STATES,
    REVIEW_PENDING,
    SignOffPacket,
    transition_release_state,
)


@dataclass(frozen=True)
class GateIssue:
    code: str
    message: str
    detail: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "detail": dict(self.detail),
        }


@dataclass(frozen=True)
class ReleaseGateResult:
    passed: bool
    current_state: str
    next_state: Optional[str]
    blockers: list[GateIssue]
    warnings: list[GateIssue]
    signoff_packet: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "passed": self.passed,
            "current_state": self.current_state,
            "next_state": self.next_state,
            "blockers": [issue.to_dict() for issue in self.blockers],
            "warnings": [issue.to_dict() for issue in self.warnings],
            "signoff_packet": dict(self.signoff_packet),
        }


def _first_present(*values: Any) -> Any:
    for value in values:
        if value not in (None, ""):
            return value
    return None


def _receipt_evidence(receipt: Mapping[str, Any]) -> Mapping[str, Any]:
    evidence = receipt.get("evidence")
    return evidence if isinstance(evidence, Mapping) else {}


def _target_from_classification(
    classification: Mapping[str, Any],
    target_scope: Mapping[str, Any] | None,
) -> Dict[str, Any]:
    evidence = _receipt_evidence(classification)
    transcript = evidence.get("transcript") if isinstance(evidence.get("transcript"), Mapping) else {}
    contributions = classification.get("contributions") or []
    evidence_classes = {
        str(row.get("evidence_class") or row.get("evidence_direction") or row.get("source"))
        for row in contributions
        if isinstance(row, Mapping)
    }
    target = {
        "variant_key": _first_present(classification.get("variant_key"), classification.get("variant_id")),
        "gene": _first_present(classification.get("gene"), transcript.get("gene")),
        "disease": _first_present(classification.get("disease"), classification.get("condition")),
        "evidence_classes": sorted(v for v in evidence_classes if v not in ("", "None")),
    }
    if target_scope:
        target.update({k: v for k, v in target_scope.items() if v not in (None, "")})
    return target


def _preflight_issue(row: Any) -> GateIssue:
    if isinstance(row, Mapping):
        name = str(row.get("name") or row.get("code") or "preflight_failure")
        message = str(row.get("message") or row.get("detail") or name)
        return GateIssue("preflight_failed", message, {"name": name})
    return GateIssue("preflight_failed", str(row), {})


def _parse_date(value: Optional[str]) -> Optional[_dt.date]:
    if not value:
        return None
    return _dt.date.fromisoformat(str(value)[:10])


def _discordance_resolved(row: Mapping[str, Any]) -> bool:
    disposition = str(row.get("disposition") or row.get("reviewer_disposition") or "").lower()
    if row.get("resolved") is True:
        return True
    if row.get("release_blocking") is False:
        return True
    return disposition in {
        "resolved",
        "accepted",
        "accepted_with_rationale",
        "non_release_blocking",
        "false_positive",
    }


def _matches(value: Any, candidate: Any) -> bool:
    if value in (None, "") or candidate in (None, ""):
        return False
    return str(value).upper() == str(candidate).upper()


def _discordance_relevant(row: Mapping[str, Any], target: Mapping[str, Any]) -> bool:
    keys = ("variant_key", "gene", "disease")
    explicit = [key for key in keys if row.get(key) not in (None, "")]
    if not explicit:
        return True
    return any(_matches(row.get(key), target.get(key)) for key in explicit)


def _unresolved_serious_discordances(
    discordances: Iterable[Mapping[str, Any]],
    target: Mapping[str, Any],
) -> list[Mapping[str, Any]]:
    unresolved = []
    for row in discordances:
        serious = bool(row.get("serious", True) or row.get("serious_discordance", False))
        if serious and _discordance_relevant(row, target) and not _discordance_resolved(row):
            unresolved.append(row)
    return unresolved


def evaluate_release_gate(
    *,
    classification: Mapping[str, Any],
    signoff_packet: Mapping[str, Any] | SignOffPacket,
    current_state: str = REVIEW_PENDING,
    target_scope: Mapping[str, Any] | None = None,
    active_config_hash: Optional[str] = None,
    preflight_failures: Iterable[Any] | None = None,
    serious_discordances: Iterable[Mapping[str, Any]] | None = None,
) -> ReleaseGateResult:
    """Evaluate whether a classification can move to ``approved_for_release``."""
    packet = (
        signoff_packet
        if isinstance(signoff_packet, SignOffPacket)
        else SignOffPacket.from_dict(signoff_packet)
    )
    target = _target_from_classification(classification, target_scope)
    blockers: list[GateIssue] = []
    warnings: list[GateIssue] = []

    if current_state not in RELEASE_STATES:
        blockers.append(GateIssue("unknown_release_state", f"unknown release state {current_state!r}"))
    else:
        try:
            transition_release_state(current_state, APPROVED_FOR_RELEASE)
        except ValueError as exc:
            blockers.append(GateIssue("illegal_release_transition", str(exc)))

    missing = packet.missing_required_fields()
    if missing:
        blockers.append(GateIssue(
            "missing_signoff_fields",
            "sign-off packet is missing required fields",
            {"fields": missing},
        ))

    scope_ok, scope_reasons = packet.clinical_scope.allows(target)
    if not scope_ok:
        blockers.extend(
            GateIssue("out_of_scope", reason, {"target": target})
            for reason in scope_reasons
        )

    if active_config_hash and packet.config_hash and packet.config_hash != active_config_hash:
        blockers.append(GateIssue(
            "config_hash_mismatch",
            "sign-off packet config hash does not match active config hash",
            {"packet_config_hash": packet.config_hash, "active_config_hash": active_config_hash},
        ))

    if disposition_blocks_release(packet.conflict_policy_disposition):
        blockers.append(GateIssue(
            "conflict_policy_unresolved",
            "conflict-policy disposition does not clear release",
            {"disposition": normalize_disposition(packet.conflict_policy_disposition)},
        ))

    for failure in preflight_failures or []:
        blockers.append(_preflight_issue(failure))

    unresolved = _unresolved_serious_discordances(serious_discordances or [], target)
    if unresolved:
        blockers.append(GateIssue(
            "unresolved_serious_discordance",
            "relevant serious discordance remains unresolved",
            {"count": len(unresolved), "discordances": [dict(row) for row in unresolved]},
        ))

    try:
        effective = _parse_date(packet.effective_date)
        rereview = _parse_date(packet.re_review_date)
    except ValueError as exc:
        blockers.append(GateIssue("invalid_signoff_date", str(exc)))
    else:
        if effective and rereview and rereview < effective:
            blockers.append(GateIssue(
                "invalid_rereview_date",
                "re-review date must not precede effective date",
                {"effective_date": packet.effective_date, "re_review_date": packet.re_review_date},
            ))

    if classification.get("tier") in {"Pathogenic", "Likely Pathogenic"} and not packet.second_reviewer:
        warnings.append(GateIssue(
            "second_review_not_recorded",
            "pathogenic-side release has no second reviewer recorded",
        ))

    passed = not blockers
    return ReleaseGateResult(
        passed=passed,
        current_state=current_state,
        next_state=APPROVED_FOR_RELEASE if passed else None,
        blockers=blockers,
        warnings=warnings,
        signoff_packet=packet.to_dict(),
    )
