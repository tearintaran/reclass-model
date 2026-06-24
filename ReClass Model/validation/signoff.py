"""Structured release sign-off packets and release-state transitions.

The engine produces draft classifications. This module describes the clinical
release workflow around those drafts: the packet a reviewer must sign, the state
machine a receipt moves through, and small helpers for scope checks. It is pure
Python so validation, API, and storage tests can exercise the policy without a
database.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Mapping, Optional, Tuple

REVIEW_PENDING = "review_pending"
APPROVED_FOR_RELEASE = "approved_for_release"
RELEASED = "released"
WITHDRAWN = "withdrawn"
RE_REVIEW_REQUIRED = "re-review_required"

RELEASE_STATES = (
    REVIEW_PENDING,
    APPROVED_FOR_RELEASE,
    RELEASED,
    WITHDRAWN,
    RE_REVIEW_REQUIRED,
)

RELEASE_STATE_TRANSITIONS = {
    REVIEW_PENDING: {APPROVED_FOR_RELEASE, WITHDRAWN, RE_REVIEW_REQUIRED},
    APPROVED_FOR_RELEASE: {RELEASED, WITHDRAWN, RE_REVIEW_REQUIRED},
    RELEASED: {RE_REVIEW_REQUIRED, WITHDRAWN},
    WITHDRAWN: set(),
    RE_REVIEW_REQUIRED: {REVIEW_PENDING, WITHDRAWN},
}

REQUIRED_SIGNOFF_FIELDS = (
    "signed_off_by",
    "clinical_scope",
    "config_hash",
    "commit",
    "source_snapshots",
    "validation_report_id",
    "conflict_policy_disposition",
    "reviewer_credential",
    "institutional_authorization",
    "effective_date",
    "re_review_date",
)

_SCOPE_ALIASES = {
    "variant_key": ("variant_key", "variant_keys", "variants"),
    "gene": ("gene", "genes"),
    "disease": ("disease", "diseases", "conditions"),
    "evidence_class": ("evidence_class", "evidence_classes"),
}


def transition_release_state(current_state: str, next_state: str) -> str:
    """Validate a release-state transition and return ``next_state``.

    The workflow intentionally has terminal withdrawal and an explicit
    re-review-required state. Re-entering the same state is idempotent so retrying
    a storage write is safe.
    """
    current = str(current_state or "").strip()
    target = str(next_state or "").strip()
    if current not in RELEASE_STATES:
        raise ValueError(f"unknown release state {current_state!r}; expected one of {RELEASE_STATES}")
    if target not in RELEASE_STATES:
        raise ValueError(f"unknown release state {next_state!r}; expected one of {RELEASE_STATES}")
    if current == target:
        return target
    if target not in RELEASE_STATE_TRANSITIONS[current]:
        raise ValueError(f"illegal release transition {current!r} -> {target!r}")
    return target


def _tuple(value: Any) -> Tuple[str, ...]:
    if value in (None, "", "*"):
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, Iterable):
        return tuple(str(item) for item in value if item not in (None, ""))
    return (str(value),)


def _first_scope_value(scope: Mapping[str, Any], aliases: Tuple[str, ...]) -> Any:
    for name in aliases:
        if name in scope:
            return scope.get(name)
    return None


@dataclass(frozen=True)
class ClinicalScope:
    """Signed clinical scope for a release packet.

    Empty allow-lists mean "not constrained by this dimension"; a populated list
    must contain the target value. This lets a lab sign a gene/disease scope
    without enumerating every future variant while still blocking explicit
    out-of-scope submissions.
    """

    active: bool = False
    variant_keys: Tuple[str, ...] = ()
    genes: Tuple[str, ...] = ()
    diseases: Tuple[str, ...] = ()
    evidence_classes: Tuple[str, ...] = ()
    name: Optional[str] = None
    authorization_id: Optional[str] = None

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any] | None) -> "ClinicalScope":
        raw = raw or {}
        return cls(
            active=bool(raw.get("active")),
            variant_keys=_tuple(_first_scope_value(raw, _SCOPE_ALIASES["variant_key"])),
            genes=_tuple(_first_scope_value(raw, _SCOPE_ALIASES["gene"])),
            diseases=_tuple(_first_scope_value(raw, _SCOPE_ALIASES["disease"])),
            evidence_classes=_tuple(_first_scope_value(raw, _SCOPE_ALIASES["evidence_class"])),
            name=raw.get("name") or raw.get("scope_name"),
            authorization_id=raw.get("authorization_id"),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "active": self.active,
            "variant_keys": list(self.variant_keys),
            "genes": list(self.genes),
            "diseases": list(self.diseases),
            "evidence_classes": list(self.evidence_classes),
            "name": self.name,
            "authorization_id": self.authorization_id,
        }

    @staticmethod
    def _contains(allowed: Tuple[str, ...], value: Any) -> bool:
        if not allowed:
            return True
        if value in (None, ""):
            return False
        normalized = {item.upper() for item in allowed}
        return str(value).upper() in normalized

    def allows(self, target: Mapping[str, Any]) -> Tuple[bool, list[str]]:
        """Return ``(allowed, reasons)`` for the target release context."""
        reasons: list[str] = []
        if not self.active:
            reasons.append("clinical scope is not active")
        checks = (
            ("variant_key", self.variant_keys),
            ("gene", self.genes),
            ("disease", self.diseases),
        )
        for key, allowed in checks:
            if not self._contains(allowed, target.get(key)):
                reasons.append(f"{key} {target.get(key)!r} is outside signed scope")
        evidence_classes = target.get("evidence_classes") or []
        if self.evidence_classes:
            missing = [
                value for value in evidence_classes
                if not self._contains(self.evidence_classes, value)
            ]
            if missing:
                reasons.append(
                    "evidence classes outside signed scope: " + ", ".join(sorted(set(missing)))
                )
        return not reasons, reasons


@dataclass(frozen=True)
class SignOffPacket:
    """Reviewer sign-off packet required by the release gate."""

    signed_off_by: Optional[str] = None
    clinical_scope: ClinicalScope = field(default_factory=ClinicalScope)
    config_hash: Optional[str] = None
    commit: Optional[str] = None
    source_snapshots: Dict[str, Any] = field(default_factory=dict)
    validation_report_id: Optional[str] = None
    conflict_policy_disposition: Optional[str] = None
    reviewer_credential: Optional[str] = None
    institutional_authorization: Optional[str] = None
    effective_date: Optional[str] = None
    re_review_date: Optional[str] = None
    reviewer_assignment: Optional[str] = None
    second_reviewer: Optional[str] = None
    second_review_at: Optional[str] = None
    second_review_required: bool = False
    override_rationale: Optional[str] = None
    release_notes: Optional[str] = None

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any] | None) -> "SignOffPacket":
        raw = raw or {}
        return cls(
            signed_off_by=raw.get("signed_off_by") or raw.get("reviewer"),
            clinical_scope=ClinicalScope.from_dict(raw.get("clinical_scope") or raw.get("scope")),
            config_hash=raw.get("config_hash"),
            commit=raw.get("commit") or raw.get("git_commit"),
            source_snapshots=dict(raw.get("source_snapshots") or {}),
            validation_report_id=raw.get("validation_report_id"),
            conflict_policy_disposition=raw.get("conflict_policy_disposition"),
            reviewer_credential=raw.get("reviewer_credential") or raw.get("credential"),
            institutional_authorization=(
                raw.get("institutional_authorization") or raw.get("lab_authorization")
            ),
            effective_date=raw.get("effective_date"),
            re_review_date=raw.get("re_review_date") or raw.get("rereview_date"),
            reviewer_assignment=raw.get("reviewer_assignment") or raw.get("assigned_reviewer"),
            second_reviewer=raw.get("second_reviewer"),
            second_review_at=raw.get("second_review_at"),
            second_review_required=bool(raw.get("second_review_required", False)),
            override_rationale=raw.get("override_rationale"),
            release_notes=raw.get("release_notes") or raw.get("release_note"),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "signed_off_by": self.signed_off_by,
            "clinical_scope": self.clinical_scope.to_dict(),
            "config_hash": self.config_hash,
            "commit": self.commit,
            "source_snapshots": dict(self.source_snapshots),
            "validation_report_id": self.validation_report_id,
            "conflict_policy_disposition": self.conflict_policy_disposition,
            "reviewer_credential": self.reviewer_credential,
            "institutional_authorization": self.institutional_authorization,
            "effective_date": self.effective_date,
            "re_review_date": self.re_review_date,
            "reviewer_assignment": self.reviewer_assignment,
            "second_reviewer": self.second_reviewer,
            "second_review_at": self.second_review_at,
            "second_review_required": self.second_review_required,
            "override_rationale": self.override_rationale,
            "release_notes": self.release_notes,
        }

    def missing_required_fields(self) -> list[str]:
        data = self.to_dict()
        missing: list[str] = []
        for field_name in REQUIRED_SIGNOFF_FIELDS:
            value = data.get(field_name)
            if value in (None, "", {}, []):
                missing.append(field_name)
        if self.second_review_required and not self.second_reviewer:
            missing.append("second_reviewer")
        return missing
