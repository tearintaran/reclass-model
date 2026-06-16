"""Continuous-reanalysis alerts (``clinical.alert``).

Only **tier crossings** create alert rows: a re-score that changes points or
evidence but leaves the tier unchanged must page no one (the defense against
alert floods). A crossing that flips between the pathogenic side
(Likely Pathogenic / Pathogenic) and the benign side (Likely Benign / Benign) is
flagged *serious*.

Two layers enforce "no non-crossing alerts": the application guard here returns
``None`` for an unchanged tier (so no INSERT is even attempted), and the schema's
``CHECK (old_tier <> new_tier)`` rejects any row that slips through. The table is
RLS-protected, so run these on a tenant-scoped session.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

# Local copy of the tier topology so this module imports only from engine/ (the
# monitoring package is owned by another job). Kept in sync with monitoring.diff.
_PATHOGENIC_SIDE = {"Likely Pathogenic", "Pathogenic"}
_BENIGN_SIDE = {"Benign", "Likely Benign"}


def is_serious_crossing(old_tier: str, new_tier: str) -> bool:
    """True iff the crossing flips between the pathogenic and benign sides."""
    return (
        (old_tier in _PATHOGENIC_SIDE and new_tier in _BENIGN_SIDE)
        or (old_tier in _BENIGN_SIDE and new_tier in _PATHOGENIC_SIDE)
    )


def insert_alert(cur, *, tenant_id: str, variant_id: str, old_tier: str,
                 new_tier: str, serious: Optional[bool] = None) -> str:
    """Insert an alert row. Raises ``ValueError`` on a non-crossing (old == new).

    The same constraint is also enforced by the schema CHECK; this guard fails
    fast before touching the database.
    """
    if old_tier == new_tier:
        raise ValueError(
            f"refusing to write a non-crossing alert (old_tier == new_tier == {old_tier!r})"
        )
    if serious is None:
        serious = is_serious_crossing(old_tier, new_tier)
    cur.execute(
        "INSERT INTO clinical.alert (tenant_id, variant_id, old_tier, new_tier, serious) "
        "VALUES (%s, %s, %s, %s, %s) RETURNING alert_id",
        (tenant_id, variant_id, old_tier, new_tier, serious),
    )
    return str(cur.fetchone()["alert_id"])


def record_rescoring(cur, *, tenant_id: str, variant_id: str, old_tier: str,
                     new_tier: str) -> Optional[str]:
    """Alert iff the tier crossed; returns the new ``alert_id`` or ``None``.

    This is the function the reanalysis pipeline should call: it embodies the
    "only crossings page" rule, returning ``None`` (no row written) when the tier
    is unchanged.
    """
    if old_tier == new_tier:
        return None
    return insert_alert(
        cur, tenant_id=tenant_id, variant_id=variant_id,
        old_tier=old_tier, new_tier=new_tier,
    )


# Alert lifecycle states (mirrors the ``alert_state`` enum in schema.sql) and the
# allowed forward transitions. ``resolved`` / ``dismissed`` are terminal.
ALERT_STATES = ("open", "acknowledged", "in_review", "resolved", "dismissed")
_TERMINAL_STATES = {"resolved", "dismissed"}
_ALLOWED_TRANSITIONS = {
    "open": {"acknowledged", "in_review", "resolved", "dismissed"},
    "acknowledged": {"in_review", "resolved", "dismissed"},
    "in_review": {"acknowledged", "resolved", "dismissed"},
    "resolved": set(),
    "dismissed": set(),
}


def update_alert_state(cur, alert_id: str, *, state: str) -> Dict[str, Any]:
    """Transition an alert to ``state`` (open/acknowledged/in_review/resolved/dismissed).

    Enforces the lifecycle: a terminal alert cannot be reopened, and only declared
    forward transitions are allowed. Entering ``resolved`` stamps ``resolved_at``.
    Raises ``ValueError`` on an unknown or illegal transition, ``LookupError`` if
    the alert is not visible to this (RLS-scoped) session.
    """
    if state not in ALERT_STATES:
        raise ValueError(f"unknown alert state {state!r}; expected one of {ALERT_STATES}")
    current = get_alert(cur, alert_id)
    if current is None:
        raise LookupError(f"alert {alert_id} not visible to this session")
    old_state = current["state"]
    if old_state == state:
        return current
    if state not in _ALLOWED_TRANSITIONS.get(old_state, set()):
        raise ValueError(
            f"illegal alert transition {old_state!r} -> {state!r}"
        )
    resolved_clause = "resolved_at = now()" if state == "resolved" else "resolved_at = resolved_at"
    cur.execute(
        f"UPDATE clinical.alert SET state = %s, {resolved_clause} "
        "WHERE alert_id = %s RETURNING *",
        (state, alert_id),
    )
    return cur.fetchone()


def record_reanalysis_event(
    cur,
    *,
    tenant_id: str,
    variant_id: str,
    old_tier: str,
    new_tier: str,
    old_points: float,
    new_points: float,
    new_classification_id: str,
    prior_classification_id: Optional[str] = None,
    trigger: str = "evidence",
    alert_id: Optional[str] = None,
    prior_bundle_id: Optional[str] = None,
    new_bundle_id: Optional[str] = None,
) -> str:
    """Append a continuous-reanalysis audit row (``clinical.reanalysis_event``).

    EVERY reanalysis outcome is recorded — including same-tier point changes
    (``crossed = False``, ``alert_id`` NULL) that intentionally page no one. The
    ``crossed`` flag is derived from the tiers (the schema CHECK enforces the
    match). ``prior_bundle_id`` / ``new_bundle_id`` (optional) capture the
    de-identified ``research.evidence_bundle`` receipts behind the old and new
    classifications, so the full evidence delta is reconstructable (gap §5 task 3).
    Must run on a tenant-scoped session.
    """
    crossed = old_tier != new_tier
    cur.execute(
        """
        INSERT INTO clinical.reanalysis_event (
            tenant_id, variant_id, prior_classification_id, new_classification_id,
            old_tier, new_tier, old_points, new_points, trigger, crossed, alert_id,
            prior_bundle_id, new_bundle_id
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING reanalysis_id
        """,
        (
            tenant_id, variant_id, prior_classification_id, new_classification_id,
            old_tier, new_tier, old_points, new_points, trigger, crossed, alert_id,
            prior_bundle_id, new_bundle_id,
        ),
    )
    return str(cur.fetchone()["reanalysis_id"])


def list_reanalysis_events(cur, *, variant_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """List reanalysis audit rows visible to the current session."""
    if variant_id is None:
        cur.execute("SELECT * FROM clinical.reanalysis_event ORDER BY created_at")
    else:
        cur.execute(
            "SELECT * FROM clinical.reanalysis_event WHERE variant_id = %s "
            "ORDER BY created_at",
            (variant_id,),
        )
    return cur.fetchall()


def get_alert(cur, alert_id: str) -> Optional[Dict[str, Any]]:
    cur.execute(
        "SELECT * FROM clinical.alert WHERE alert_id = %s", (alert_id,)
    )
    return cur.fetchone()


def list_alerts(cur, *, variant_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """List alerts visible to the current session (subject to RLS)."""
    if variant_id is None:
        cur.execute("SELECT * FROM clinical.alert ORDER BY created_at")
    else:
        cur.execute(
            "SELECT * FROM clinical.alert WHERE variant_id = %s ORDER BY created_at",
            (variant_id,),
        )
    return cur.fetchall()
