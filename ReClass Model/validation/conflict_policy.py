"""Offline conflict-policy checks for validation and reviewer triage."""

from __future__ import annotations

from typing import Any

from engine import config as C

FREQUENCY_BENIGN_CRITERIA = {"BA1", "BS1"}
NON_CURATED_PATHOGENIC_SOURCES = {
    "gnomad",
    "revel",
    "alphamissense",
    "computational",
    "conservation",
}

CONFLICT_DISPOSITIONS = (
    "no_conflict",
    "resolved",
    "exception_signed",
    "accepted_with_rationale",
    "unresolved",
    "rejected",
)
RELEASE_CLEARING_DISPOSITIONS = {
    "no_conflict",
    "resolved",
    "exception_signed",
    "accepted_with_rationale",
}


def _as_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if hasattr(value, "__dict__"):
        return dict(value.__dict__)
    return {}


def _rows(values: Any) -> list[dict[str, Any]]:
    out = []
    for value in values or []:
        row = _as_dict(value)
        if row:
            out.append(row)
    return out


def _criterion(row: dict[str, Any]) -> str:
    return str(row.get("acmg_criterion") or row.get("criterion") or "").upper()


def _direction(row: dict[str, Any]) -> str | None:
    direction = row.get("evidence_direction") or row.get("direction")
    if direction:
        return str(direction).lower()
    criterion = _criterion(row)
    if criterion.startswith("P"):
        return "pathogenic"
    if criterion.startswith("B"):
        return "benign"
    return None


def _source(row: dict[str, Any]) -> str:
    return str(row.get("source") or "").lower()


def _variant_key(*records: Any) -> str | None:
    for record in records:
        row = _as_dict(record)
        key = row.get("variant_key") or row.get("variant_id")
        if key:
            return str(key)
    return None


def collect_evidence_rows(
    classification: dict[str, Any] | None = None,
    evidence_record: Any = None,
) -> list[dict[str, Any]]:
    """Collect criteria/events from a classification, bundle, or fixture case."""
    rows: list[dict[str, Any]] = []
    cls = _as_dict(classification)
    rows.extend(_rows(cls.get("contributions")))

    evidence = _as_dict(evidence_record)
    rows.extend(_rows(evidence.get("events")))
    rows.extend(_rows(evidence.get("criteria")))
    signals = evidence.get("signals") or {}
    if isinstance(signals, dict):
        rows.extend(_rows(signals.get("criteria")))
    return rows


def _is_frequency_benign(row: dict[str, Any]) -> bool:
    return _criterion(row) in FREQUENCY_BENIGN_CRITERIA and _direction(row) == "benign"


def _is_curated_pathogenic(row: dict[str, Any]) -> bool:
    if _direction(row) != "pathogenic":
        return False
    criterion = _criterion(row)
    if not criterion.startswith("P"):
        return False
    source = _source(row)
    return source not in NON_CURATED_PATHOGENIC_SOURCES


def _exception_codes(exception: dict[str, Any]) -> set[str]:
    raw = exception.get("conflict_codes") or exception.get("conflict_code") or exception.get("rule")
    if raw is None:
        return set()
    if isinstance(raw, str):
        return {raw}
    return {str(item) for item in raw}


def _has_signature(exception: dict[str, Any]) -> bool:
    sign_off = exception.get("sign_off") or {}
    return bool(
        exception.get("signed")
        or exception.get("signature")
        or exception.get("signed_off_by")
        or sign_off.get("signed_off_by")
    )


def normalize_disposition(disposition: Any) -> str:
    """Normalize a conflict-policy disposition into the release-gate vocabulary."""
    value = str(disposition or "").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "none": "no_conflict",
        "pass": "no_conflict",
        "cleared": "resolved",
        "clear": "resolved",
        "signed_exception": "exception_signed",
        "signed_variant_exception": "exception_signed",
        "accepted": "accepted_with_rationale",
    }
    return aliases.get(value, value)


def disposition_blocks_release(disposition: Any) -> bool:
    """True when a conflict-policy disposition is absent or release-blocking."""
    normalized = normalize_disposition(disposition)
    return normalized not in RELEASE_CLEARING_DISPOSITIONS


def _signed_variant_exception(
    exception: dict[str, Any],
    *,
    variant_key: str | None,
    issue_code: str,
) -> bool:
    if str(exception.get("scope") or "variant_specific") != "variant_specific":
        return False
    if variant_key and str(exception.get("variant_key")) != variant_key:
        return False
    codes = _exception_codes(exception)
    if codes and issue_code not in codes and "BA1_BS1_CURATED_PATHOGENIC" not in codes:
        return False
    return _has_signature(exception)


def evaluate_conflict_policy(
    *,
    classification: dict[str, Any] | None = None,
    evidence_record: Any = None,
    exceptions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Evaluate configurable frequency-vs-curated-pathogenic conflict checks."""
    rows = collect_evidence_rows(classification, evidence_record)
    variant_key = _variant_key(classification, evidence_record)
    frequency_rows = [row for row in rows if _is_frequency_benign(row)]
    pathogenic_rows = [row for row in rows if _is_curated_pathogenic(row)]
    exceptions = list(exceptions or [])

    violations = []
    cleared = []
    for freq in frequency_rows:
        for patho in pathogenic_rows:
            issue_code = f"{_criterion(freq)}_CURATED_PATHOGENIC"
            record = {
                "rule": "frequency_benign_vs_curated_pathogenic",
                "issue_code": issue_code,
                "variant_key": variant_key,
                "frequency_criterion": _criterion(freq),
                "pathogenic_criterion": _criterion(patho),
                "frequency_source": freq.get("source"),
                "pathogenic_source": patho.get("source"),
                "message": (
                    f"{_criterion(freq)} benign-frequency evidence collides with "
                    f"curated pathogenic evidence {_criterion(patho)}."
                ),
            }
            exception = next(
                (
                    exc
                    for exc in exceptions
                    if _signed_variant_exception(exc, variant_key=variant_key, issue_code=issue_code)
                ),
                None,
            )
            if exception:
                cleared.append({**record, "exception_id": exception.get("exception_id")})
            else:
                violations.append(record)

    return {
        "status": "pass" if not violations else "fail",
        "variant_key": variant_key,
        "checked_rules": ["frequency_benign_vs_curated_pathogenic"],
        "violations": violations,
        "cleared_by_exceptions": cleared,
        "global_threshold_mutated": False,
        "global_thresholds": {"ba1_af": C.BA1_AF, "bs1_af": C.BS1_AF},
    }
