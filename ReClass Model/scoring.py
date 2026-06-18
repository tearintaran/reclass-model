"""Backwards-compatibility shim — the scoring engine lives in ``engine/scoring.py``.

This module used to carry a full *copy* of the engine, which silently drifted out of
sync with ``engine/scoring.py`` (gap.md D1). It is now a thin re-export so any legacy
``import scoring`` keeps working while there is exactly ONE implementation. Do not add
logic here; edit ``engine/scoring.py``.
"""

from __future__ import annotations

from engine.scoring import *  # noqa: F401,F403  (re-export the public engine surface)
from engine.scoring import (  # noqa: F401  explicit re-export of the canonical names
    Classification,
    Contribution,
    EvidenceEvent,
    classify,
    classify_signals,
    derive_criteria_from_signals,
    derive_extended_criteria,
    reconstruction_hash,
)
