"""Evidence-coverage model and roll-ups (job1 task 2).

The validation gap is evidence *completeness*: the engine reproduces expert tiers
when fed complete evidence and fails when criteria are missing. This module turns
"which criteria does this variant actually have evidence for?" into a measurable,
sliceable surface so operators can see **which cases are blocked by missing
evidence**, broken down by gene, VCEP, disease, variant class, and provider.

The logic is pure (no I/O, no wall clock). A :class:`CoverageRecord` describes one
(variant, context) and the ACMG criteria it currently has evidence for;
:func:`compute_coverage` derives the missing *categories* and whether the case is
blocked; :func:`rollup` / :func:`summarize` aggregate a set of records into the
blocked-case breakdowns the API and the dashboard read. Persistence (the tenant-
scoped ``clinical.evidence_coverage`` table) lives in ``storage.evidence``; this
module never touches a database.

Expected-criteria sets per variant class are *reviewable defaults* reconstructed
from ACMG/AMP practice (which criteria one would normally evaluate for that class).
They scope the denominator of a coverage measurement; confirm them against the
validated clinical scope before clinical use. They never change a classification —
the engine still sums only the evidence that is actually present.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Set

#: ACMG criteria grouped into the evidence *categories* coverage is measured over.
#: A category is "covered" when at least one of its member criteria has evidence.
CATEGORY_MEMBERS: Dict[str, Set[str]] = {
    "lof": {"PVS1"},
    "computational": {"PP3", "BP4"},
    "functional": {"PS3", "BS3"},
    "phasing": {"PM3", "BP2"},
    "segregation": {"PP1", "BS4"},
    "phenotype": {"PP4"},
    "case_control": {"PS4"},
    "frequency": {"PM2", "BA1", "BS1"},
    "de_novo": {"PS2", "PM6"},
    "mechanism": {"PP2", "BP1"},
}

#: Reviewable per-class expectation of which categories should be evaluated.
EXPECTED_CATEGORIES: Dict[str, List[str]] = {
    "missense": ["computational", "functional", "phasing", "segregation",
                 "phenotype", "case_control", "frequency", "de_novo", "mechanism"],
    "lof": ["lof", "functional", "phasing", "segregation", "case_control",
            "frequency", "de_novo"],
    "nonsense": ["lof", "functional", "phasing", "segregation", "frequency"],
    "frameshift": ["lof", "functional", "phasing", "segregation", "frequency"],
    "splice": ["lof", "functional", "phasing", "segregation", "frequency"],
    "indel": ["functional", "phasing", "segregation", "frequency"],
    "synonymous": ["computational", "functional", "frequency"],
    "default": ["functional", "phasing", "segregation", "phenotype",
                "case_control", "frequency"],
}

#: Categories whose absence commonly BLOCKS a case from moving off VUS. A case is
#: "blocked" when it is missing at least one expected blocking category. These are
#: the high-value, reviewer/pipeline-supplied evidence types the workbench captures.
BLOCKING_CATEGORIES: Set[str] = {"lof", "functional", "segregation", "case_control", "phasing"}

#: The dimensions a coverage roll-up can be sliced by.
DIMENSIONS = ("gene", "vcep", "disease", "variant_class", "provider")


def category_of(criterion: str) -> Optional[str]:
    """The coverage category a criterion belongs to (None if uncategorized)."""
    c = str(criterion).upper()
    for category, members in CATEGORY_MEMBERS.items():
        if c in members:
            return category
    return None


def expected_categories(variant_class: Optional[str]) -> List[str]:
    """Reviewable expected categories for a variant class (falls back to default)."""
    key = (variant_class or "").strip().lower()
    return list(EXPECTED_CATEGORIES.get(key, EXPECTED_CATEGORIES["default"]))


def present_categories(criteria: Iterable[str]) -> Set[str]:
    """The set of coverage categories that have at least one present criterion."""
    out: Set[str] = set()
    for c in criteria:
        cat = category_of(c)
        if cat is not None:
            out.add(cat)
    return out


@dataclass
class CoverageRecord:
    """Evidence coverage for one (variant, context). Pure data — no persistence.

    ``present_criteria`` is the criteria the variant currently has evidence for
    (from resolved providers + reviewer-entered evidence). ``missing_categories``
    and ``blocked`` are derived by :func:`compute_coverage` unless supplied.
    """

    variant_key: str
    gene: Optional[str] = None
    vcep: Optional[str] = None
    disease: Optional[str] = None
    variant_class: Optional[str] = None
    provider: Optional[str] = None
    present_criteria: List[str] = field(default_factory=list)
    missing_categories: List[str] = field(default_factory=list)
    blocked: bool = False
    blocking_reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "variant_key": self.variant_key,
            "gene": self.gene,
            "vcep": self.vcep,
            "disease": self.disease,
            "variant_class": self.variant_class,
            "provider": self.provider,
            "present_criteria": list(self.present_criteria),
            "missing_categories": list(self.missing_categories),
            "blocked": self.blocked,
            "blocking_reason": self.blocking_reason,
        }


def compute_coverage(
    variant_key: str,
    present_criteria: Iterable[str],
    *,
    variant_class: Optional[str] = None,
    gene: Optional[str] = None,
    vcep: Optional[str] = None,
    disease: Optional[str] = None,
    provider: Optional[str] = None,
) -> CoverageRecord:
    """Derive the missing categories and blocked status for one variant."""
    present_list = [str(c).upper() for c in present_criteria]
    present = present_categories(present_list)
    expected = expected_categories(variant_class)
    missing = [cat for cat in expected if cat not in present]
    blocking_missing = [cat for cat in missing if cat in BLOCKING_CATEGORIES]
    blocked = bool(blocking_missing)
    reason = (
        "missing blocking evidence: " + ", ".join(blocking_missing)
        if blocking_missing else None
    )
    return CoverageRecord(
        variant_key=variant_key, gene=gene, vcep=vcep, disease=disease,
        variant_class=variant_class, provider=provider,
        present_criteria=present_list, missing_categories=missing,
        blocked=blocked, blocking_reason=reason,
    )


def _record_value(record: Any, dimension: str) -> Optional[str]:
    if isinstance(record, CoverageRecord):
        return getattr(record, dimension)
    if isinstance(record, dict):
        return record.get(dimension)
    return getattr(record, dimension, None)


def _record_blocked(record: Any) -> bool:
    if isinstance(record, CoverageRecord):
        return record.blocked
    if isinstance(record, dict):
        return bool(record.get("blocked"))
    return bool(getattr(record, "blocked", False))


def _record_missing(record: Any) -> List[str]:
    if isinstance(record, CoverageRecord):
        return list(record.missing_categories)
    if isinstance(record, dict):
        # Accept both the model key (missing_categories) and the DB column
        # (missing_criteria) so a roll-up works over either source.
        return list(record.get("missing_categories") or record.get("missing_criteria") or [])
    return list(getattr(record, "missing_categories", []) or [])


def rollup(records: Iterable[Any], by: str) -> Dict[str, Dict[str, Any]]:
    """Aggregate coverage records into a blocked-case breakdown along one dimension.

    ``by`` is one of :data:`DIMENSIONS`. Records missing that dimension are bucketed
    under ``"(unspecified)"`` so nothing is silently dropped. Each bucket reports the
    total, blocked count, block rate, and the most common missing categories.
    """
    if by not in DIMENSIONS:
        raise ValueError(f"coverage rollup dimension must be one of {DIMENSIONS}, got {by!r}")
    buckets: Dict[str, Dict[str, Any]] = {}
    for record in records:
        value = _record_value(record, by) or "(unspecified)"
        bucket = buckets.setdefault(value, {
            "total": 0, "blocked": 0, "missing_categories": Counter(),
        })
        bucket["total"] += 1
        if _record_blocked(record):
            bucket["blocked"] += 1
        bucket["missing_categories"].update(_record_missing(record))
    out: Dict[str, Dict[str, Any]] = {}
    for value, bucket in buckets.items():
        total = bucket["total"]
        out[value] = {
            "total": total,
            "blocked": bucket["blocked"],
            "block_rate": round(bucket["blocked"] / total, 4) if total else 0.0,
            "missing_categories": dict(bucket["missing_categories"].most_common()),
        }
    return out


def summarize(records: Iterable[Any]) -> Dict[str, Any]:
    """Overall coverage stats plus a per-dimension blocked-case breakdown.

    Returns ``{total, blocked, block_rate, by: {dimension: rollup}}`` for every
    dimension in :data:`DIMENSIONS`, so a single call backs the whole dashboard.
    """
    records = list(records)
    total = len(records)
    blocked = sum(1 for r in records if _record_blocked(r))
    return {
        "total": total,
        "blocked": blocked,
        "block_rate": round(blocked / total, 4) if total else 0.0,
        "by": {dim: rollup(records, dim) for dim in DIMENSIONS},
    }
