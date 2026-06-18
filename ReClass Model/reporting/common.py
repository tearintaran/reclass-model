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


def receipt_evidence(receipt: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The resolved evidence bundle dict persisted on a receipt (or ``None``).

    Receipts created from a resolved bundle carry it under ``evidence`` (job1
    transcript identity + PS4 cohort counts + provenance); receipts scored from
    direct events/signals carry ``None``.
    """
    ev = (receipt or {}).get("evidence")
    return ev if isinstance(ev, dict) else None


def transcript_fields(evidence: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Report-facing transcript identity pulled off an evidence bundle dict.

    Returns ``{transcript, gene, hgvs_c, hgvs_p}`` where ``transcript`` is the MANE
    Select id (falling back to RefSeq) -- the form the FHIR serializer and clinical
    report want. Every value is ``None`` when no transcript identity was resolved,
    so absence is rendered as unknown, never invented (job1 task 4).
    """
    tx = (evidence or {}).get("transcript") or {}
    return {
        "transcript": tx.get("mane_select") or tx.get("refseq"),
        "gene": tx.get("gene"),
        "hgvs_c": tx.get("hgvs_c"),
        "hgvs_p": tx.get("hgvs_p"),
    }


# Labels that mark a value as an expert-panel grouping rather than a true ancestry,
# so a VCEP name carried in a legacy ``ancestry`` field is never rendered as an
# ancestry stratum (job1 task 5).
_PANEL_MARKERS = ("vcep", "panel", "working group")


def _looks_like_panel(value: Any) -> bool:
    if not value:
        return False
    s = str(value).strip().lower()
    return any(m in s for m in _PANEL_MARKERS)


def stratification_block(source: Dict[str, Any]) -> Dict[str, Any]:
    """Separate true ancestry/population from VCEP/expert-panel grouping (task 5).

    These two field families were historically conflated in a single ``ancestry``
    field (ClinGen stuffed the VCEP name there). This returns them as DISTINCT,
    documented fields so any equity/stratification analysis reads the population
    family, never a panel name:

      * ``population``  -- true genetic-ancestry / population-stratification group,
      * ``vcep_group``  -- ClinGen VCEP / expert-panel grouping (NOT an ancestry).

    ``legacy_ancestry`` echoes any old ``ancestry`` value for traceability, and
    ``legacy_ancestry_is_panel`` flags when that legacy value actually held a panel
    name (so the conflation is visible, not silently re-presented as an ancestry).
    """
    population = source.get("population")
    vcep_group = source.get("vcep_group")
    legacy = source.get("ancestry")
    # Back-fill from the legacy field only when the dedicated fields are absent.
    if population is None and vcep_group is None and legacy is not None:
        if _looks_like_panel(legacy):
            vcep_group = legacy
        elif str(legacy).strip().lower() not in ("", "unspecified", "unknown", "none", "na"):
            population = legacy
    return {
        "population": population,
        "vcep_group": vcep_group,
        "legacy_ancestry": legacy,
        "legacy_ancestry_is_panel": _looks_like_panel(legacy),
    }


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
