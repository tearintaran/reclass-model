"""Batch importers for validated upstream evidence sources (job1 task 4).

Reviewers and validated pipelines need to load evidence in bulk: a lab's functional
assay export, a phenotype/HPO match table, family segregation / de novo records, a
case-control cohort. This importer routes each row through the existing, provenance-
rich ``evidence.upstream`` adapters — so a batch import emits exactly the same
standardized :class:`~evidence.model.EvidenceBundle` (criterion, strength, source,
version, checksum, access date) as a single resolve, and the engine sums it
identically.

**No raw PHI is written into research tables.** Research evidence is de-identified by
construction (keyed only on the public ``variant_key``), so before a row is mapped
its payload is run through :func:`scrub_phi`, which drops known patient-identifying
fields and records that it did so. A batch import that carried an ``mrn`` or
``patient_name`` therefore stores the de-identified structured evidence and a warning
that the identifier was dropped — it never persists the identifier.

The importer is pure and offline: ``access_date`` comes from the batch (or each row),
never the wall clock, so a re-import of the same rows yields the same bundles. The
returned report is a dry-run-friendly summary; persisting the bundles to
``research.*`` is a separate, explicit step (``storage.evidence.insert_evidence_bundle``).
"""

from __future__ import annotations

import os
import sys
from typing import Any, Dict, Iterable, List, Optional, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from engine.normalize import parse_key  # noqa: E402
from evidence.model import EvidenceBundle  # noqa: E402
from evidence.upstream import (  # noqa: E402
    CaseControlAdapter,
    DeNovoAdapter,
    DiseaseMechanismAdapter,
    FunctionalAssayAdapter,
    PhasingAdapter,
    PhenotypeAdapter,
    SegregationAdapter,
)

#: Patient-identifying fields that must never reach a research table. Matched
#: case-insensitively against a row's payload keys and dropped before mapping.
PHI_KEYS = frozenset({
    "mrn", "medical_record_number", "patient_id", "patient_name", "name",
    "first_name", "last_name", "dob", "date_of_birth", "birth_date", "ssn",
    "address", "phone", "phone_number", "email", "zip", "postal_code",
})

#: Evidence type -> upstream adapter class.
ADAPTER_BY_TYPE = {
    "functional": FunctionalAssayAdapter,
    "phenotype": PhenotypeAdapter,
    "segregation": SegregationAdapter,
    "de_novo": DeNovoAdapter,
    "phasing": PhasingAdapter,
    "case_control": CaseControlAdapter,
    "disease_mechanism": DiseaseMechanismAdapter,
}

#: High-level source kind -> default evidence type.
SOURCE_KIND_DEFAULT_TYPE = {
    "functional": "functional",
    "phenotype": "phenotype",
    "family": "segregation",
    "cohort": "case_control",
    "lab": "functional",
}

#: Evidence types a source kind may carry (a row may override the default within this).
SOURCE_KIND_ALLOWED_TYPES = {
    "functional": {"functional"},
    "phenotype": {"phenotype"},
    "family": {"segregation", "de_novo", "phasing"},
    "cohort": {"case_control"},
    "lab": {"functional", "phenotype", "case_control"},
}


class BatchImportError(ValueError):
    """Raised when a batch's source kind or evidence type is unknown."""


def scrub_phi(payload: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    """Drop known patient-identifying fields from a payload (top-level + nested).

    Returns ``(clean_payload, dropped_keys)``. Nested ``patient`` / ``sample`` blocks
    are removed wholesale (they are PHI containers). Nothing is guessed: only the
    enumerated :data:`PHI_KEYS` and PHI containers are removed; all de-identified
    structured evidence is preserved verbatim.
    """
    dropped: List[str] = []
    clean: Dict[str, Any] = {}
    for key, value in payload.items():
        lk = str(key).lower()
        if lk in PHI_KEYS:
            dropped.append(str(key))
            continue
        if lk in ("patient", "sample", "subject") and isinstance(value, dict):
            dropped.append(str(key))
            continue
        clean[key] = value
    return clean, dropped


def _evidence_type_of(record: Dict[str, Any], source_kind: str) -> str:
    """Resolve the evidence type a row targets (row override, else the kind default)."""
    requested = record.get("evidence_type") or SOURCE_KIND_DEFAULT_TYPE.get(source_kind)
    if requested not in ADAPTER_BY_TYPE:
        raise BatchImportError(
            f"unknown evidence type {requested!r} (known: {sorted(ADAPTER_BY_TYPE)})"
        )
    allowed = SOURCE_KIND_ALLOWED_TYPES.get(source_kind)
    if allowed is not None and requested not in allowed:
        raise BatchImportError(
            f"source kind {source_kind!r} does not carry evidence type {requested!r} "
            f"(allowed: {sorted(allowed)})"
        )
    return requested


def _payload_of(record: Dict[str, Any], evidence_type: str) -> Dict[str, Any]:
    """The evidence payload for a row: a named block, else the row's evidence fields."""
    block = record.get(evidence_type)
    if isinstance(block, dict):
        return dict(block)
    skip = {"variant_key", "gene", "locus", "evidence", "evidence_type", evidence_type}
    return {k: v for k, v in record.items() if k not in skip}


def _locus_of(variant_key: Optional[str]) -> Optional[Dict[str, Any]]:
    if not variant_key:
        return None
    try:
        p = parse_key(variant_key)
    except ValueError:
        return None
    return {"chrom": p["chrom"], "pos": p["pos"], "ref": p["ref"], "alt": p["alt"]}


def import_record(
    record: Dict[str, Any],
    source_kind: str,
    *,
    access_date: Optional[str] = None,
) -> Tuple[EvidenceBundle, Dict[str, Any]]:
    """Import one row into an :class:`EvidenceBundle` + a per-row report entry.

    The row's payload is PHI-scrubbed, routed through the upstream adapter for its
    evidence type, and re-keyed under the row's ``variant_key`` so the bundle carries
    the identity the caller supplied.
    """
    variant_key = record.get("variant_key")
    evidence_type = _evidence_type_of(record, source_kind)
    raw_payload = _payload_of(record, evidence_type)
    payload, dropped = scrub_phi(raw_payload)

    adapter = ADAPTER_BY_TYPE[evidence_type](access_date=access_date)
    case: Dict[str, Any] = {"evidence": {evidence_type: payload}}
    locus = _locus_of(variant_key)
    if locus is not None:
        case["locus"] = locus
    bundle = adapter.fetch(case)
    # Re-key the bundle under the caller's variant_key (the adapter derives a canonical
    # key from the locus, which may differ in build prefix from the supplied key).
    if variant_key is not None:
        bundle.variant_key = str(variant_key)
    if dropped:
        bundle.warnings.append("phi_fields_dropped:" + ",".join(sorted(dropped)))

    status = (bundle.match or {}).get("status", "absent")
    entry = {
        "variant_key": variant_key,
        "gene": record.get("gene"),
        "evidence_type": evidence_type,
        "status": status,
        "called": bool(bundle.events),
        "criteria": [e.acmg_criterion for e in bundle.events],
        "cohort_counts": bundle.cohort_counts.to_dict() if bundle.cohort_counts else None,
        "phi_fields_dropped": sorted(dropped),
        "warnings": list(bundle.warnings),
    }
    return bundle, entry


def import_batch(
    source_kind: str,
    records: Iterable[Dict[str, Any]],
    *,
    access_date: Optional[str] = None,
) -> Dict[str, Any]:
    """Import a batch of evidence rows; return ``(bundles, report)`` as a dict.

    ``report`` summarizes the import: total rows, how many produced a called
    criterion vs. an explicit no-call, the de-identification (PHI fields dropped), and
    a per-row entry. ``bundles`` are the standardized de-identified bundles, ready to
    persist via ``storage.evidence.insert_evidence_bundle`` (a separate step).
    """
    if source_kind not in SOURCE_KIND_DEFAULT_TYPE:
        raise BatchImportError(
            f"unknown source kind {source_kind!r} (known: {sorted(SOURCE_KIND_DEFAULT_TYPE)})"
        )
    bundles: List[EvidenceBundle] = []
    entries: List[Dict[str, Any]] = []
    called = 0
    phi_dropped_total = 0
    for record in records:
        if not isinstance(record, dict):
            entries.append({"status": "malformed", "called": False,
                            "warnings": ["row_not_an_object"]})
            continue
        bundle, entry = import_record(record, source_kind, access_date=access_date)
        bundles.append(bundle)
        entries.append(entry)
        if entry["called"]:
            called += 1
        phi_dropped_total += len(entry.get("phi_fields_dropped") or [])

    report = {
        "source_kind": source_kind,
        "access_date": access_date,
        "total": len(entries),
        "called": called,
        "no_call": len(entries) - called,
        "phi_fields_dropped": phi_dropped_total,
        "records": entries,
    }
    return {"bundles": bundles, "report": report}
