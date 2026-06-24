"""Continuous reanalysis tier-crossing alert logic (spec 06 / memo S9).

Only TIER CROSSINGS alert. A re-score that changes points or evidence but leaves
the tier unchanged is recorded but pages no one — this is the explicit defense
against alert floods when a data source releases. A crossing between a
pathogenic-side tier and a benign-side tier is flagged SERIOUS.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

TIER_ORDER = ["Benign", "Likely Benign", "VUS", "Likely Pathogenic", "Pathogenic"]
_RANK = {t: i for i, t in enumerate(TIER_ORDER)}
_PATHOGENIC_SIDE = {"Likely Pathogenic", "Pathogenic"}
_BENIGN_SIDE = {"Benign", "Likely Benign"}


@dataclass(frozen=True)
class Alert:
    old_tier: str
    new_tier: str
    direction: str  # "upgrade" | "downgrade"
    steps: int
    serious: bool

    def __str__(self) -> str:
        sev = "SERIOUS " if self.serious else ""
        return f"{sev}{self.direction}: {self.old_tier} -> {self.new_tier} ({self.steps} step(s))"


@dataclass(frozen=True)
class SameTierChange:
    old_tier: str
    new_tier: str
    old_points: float
    new_points: float
    delta_points: float
    alert: bool = False


def _validate(tier: str) -> None:
    if tier not in _RANK:
        raise ValueError(f"unknown tier: {tier!r} (expected one of {TIER_ORDER})")


def is_serious_crossing(old_tier: str, new_tier: str) -> bool:
    """True iff the crossing flips between pathogenic-side and benign-side."""
    _validate(old_tier)
    _validate(new_tier)
    return (
        (old_tier in _PATHOGENIC_SIDE and new_tier in _BENIGN_SIDE)
        or (old_tier in _BENIGN_SIDE and new_tier in _PATHOGENIC_SIDE)
    )


def diff(old_tier: str, new_tier: str) -> Optional[Alert]:
    """Return an Alert iff the tier changed; None means no crossing (no page)."""
    _validate(old_tier)
    _validate(new_tier)
    if old_tier == new_tier:
        return None
    steps = _RANK[new_tier] - _RANK[old_tier]
    return Alert(
        old_tier=old_tier,
        new_tier=new_tier,
        direction="upgrade" if steps > 0 else "downgrade",
        steps=abs(steps),
        serious=is_serious_crossing(old_tier, new_tier),
    )


def same_tier_audit(
    old_tier: str,
    new_tier: str,
    old_points: float,
    new_points: float,
) -> Optional[SameTierChange]:
    """Return audit metadata for same-tier point/evidence changes; never alerts."""
    _validate(old_tier)
    _validate(new_tier)
    if old_tier != new_tier or old_points == new_points:
        return None
    return SameTierChange(
        old_tier=old_tier,
        new_tier=new_tier,
        old_points=float(old_points),
        new_points=float(new_points),
        delta_points=float(new_points) - float(old_points),
    )
