"""Patient-safe summary report builder.

A plain-language summary of the classification *result* and its limitations. It
deliberately omits the per-criterion point arithmetic and contains NO treatment
directives or management recommendations — it describes what the classification
is and what it is not, and defers all clinical decisions to the care team.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .common import (
    DEFAULT_LIMITATIONS,
    identity_block,
    now_iso,
    release_status,
)

# Neutral, non-directive plain-language descriptions of each tier. These describe
# the classification only; they prescribe nothing.
_TIER_PLAIN_LANGUAGE: Dict[str, str] = {
    "Pathogenic": "This variant is classified as disease-causing based on the "
                  "available standardized evidence.",
    "Likely Pathogenic": "This variant is classified as probably disease-causing "
                         "based on the available standardized evidence.",
    "VUS": "This variant is classified as a variant of uncertain significance: "
           "the available evidence is not sufficient to determine whether it is "
           "disease-causing.",
    "Likely Benign": "This variant is classified as probably not disease-causing "
                     "based on the available standardized evidence.",
    "Benign": "This variant is classified as not disease-causing based on the "
              "available standardized evidence.",
}


def tier_plain_language(tier: Optional[str]) -> str:
    return _TIER_PLAIN_LANGUAGE.get(
        tier or "",
        "The classification for this variant is not available.",
    )


def build_patient_summary(
    *,
    classification: Dict[str, Any],
    normalized_identity: Optional[Dict[str, Any]] = None,
    limitations: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Build the structured patient-safe summary (see module docstring)."""
    status = release_status(classification)
    tier = classification.get("tier")
    return {
        "report_type": "patient_summary",
        "generated_utc": now_iso(),
        "release_status": status,
        "identity": identity_block(classification, normalized_identity),
        "result": {
            "classification": tier,
            "plain_language": tier_plain_language(tier),
        },
        "what_this_means": (
            "A genetic variant classification summarizes how current standardized "
            "evidence is interpreted. It can change over time as new evidence "
            "becomes available."
        ),
        "review_status": (
            "This summary has been reviewed and signed off by a qualified clinician."
            if not status["is_draft"]
            else "This summary is a draft and has not yet been reviewed and signed "
                 "off by a qualified clinician; it is not for clinical use."
        ),
        "next_steps": (
            "Please discuss this result with your healthcare provider, who can "
            "explain what it means in the context of your personal and family "
            "health history."
        ),
        "limitations": list(limitations) if limitations is not None else list(DEFAULT_LIMITATIONS),
    }
