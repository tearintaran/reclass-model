"""Shared building blocks for the reporting layer.

Kept separate so the reviewer and patient-summary builders share one definition
of the standing limitations/disclaimer language and the small helpers that read a
classification receipt without caring whether it came from the DB store or the
in-memory store.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# Standing limitations every generated report must carry. These are deliberately
# framed as decision-support caveats — never as clinical or treatment guidance.
DEFAULT_LIMITATIONS: List[str] = [
    "This report is automated decision support and is not a clinical "
    "interpretation on its own.",
    "The scoring configuration is reconstructed from documented ACMG/AMP/SVI "
    "assumptions and must be clinically reviewed before real-world use.",
    "Evidence completeness varies by source; absent evidence is reported as "
    "unknown, not as evidence of benignity or pathogenicity.",
    "A result is not released for clinical use until a credentialed human "
    "reviewer signs it off.",
]


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def release_status(classification: Dict[str, Any]) -> Dict[str, Any]:
    """Draft-vs-signed release state derived from a receipt's sign-off fields."""
    signer = classification.get("signed_off_by")
    is_draft = signer in (None, "")
    return {
        "is_draft": is_draft,
        "status": "DRAFT — not for clinical use" if is_draft else "SIGNED OFF",
        "signed_off_by": signer,
        "signed_off_at": classification.get("signed_off_at"),
    }


def bundle_to_dict(evidence_bundle: Any) -> Optional[Dict[str, Any]]:
    """Accept an ``EvidenceBundle`` or its dict form (or None) -> dict or None."""
    if evidence_bundle is None:
        return None
    if hasattr(evidence_bundle, "to_dict"):
        return evidence_bundle.to_dict()
    return dict(evidence_bundle)


def identity_block(
    classification: Dict[str, Any],
    normalized_identity: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Normalized/canonical identity for the report header.

    ``normalized_identity`` (when supplied by the caller, e.g. the normalize
    layer) takes precedence; otherwise we fall back to whatever identity fields
    the receipt carries (``variant_key`` / ``variant_id``).
    """
    block: Dict[str, Any] = {
        "variant_key": classification.get("variant_key"),
        "variant_id": classification.get("variant_id"),
    }
    if normalized_identity:
        block.update(normalized_identity)
    return block
