"""Enrich the ClinVar benchmark with ClinGen-applied ACMG criteria (gap.md 1A).

Pipeline:

    clinvar_real_v1 case
      -> match clingen_real_v1 by ClinVar Variation ID
      -> append the VCEP-applied ACMG criteria (with provenance) to signals.criteria
      -> preserve every original ClinVar field and its expected label
      -> write validation/fixtures/clinvar_enriched_v1.json

This recovers the structured evidence ClinVar does not publish, *without* touching
the engine or the expected labels: the benchmark's expected tier stays the ClinVar
label; only the input evidence grows. Concordance then measures how much of the gap
was missing evidence rather than scoring logic.

Run from ``ReClass Model/``:

    ../.venv/bin/python evidence/enrich_clinvar.py
"""

from __future__ import annotations

import copy
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from evidence.clingen import (  # noqa: E402
    ClinGenEvidenceProvider,
    PROVIDER_NAME,
    PROVIDER_VERSION,
    event_to_criterion,
)
from engine.normalize import audit_loci, locus_from_case  # noqa: E402
from engine.reference_cache import load_default_reference  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FIXTURES = os.path.join(ROOT, "validation", "fixtures")
CLINVAR_FIXTURE = os.path.join(FIXTURES, "clinvar_real_v1.json")
CLINGEN_FIXTURE = os.path.join(FIXTURES, "clingen_real_v1.json")
OUT = os.path.join(FIXTURES, "clinvar_enriched_v1.json")

BENCHMARK_NAME = "clinvar_enriched_v1"


# Per-case match-detail bucket -> the report column it rolls up into. A canonical
# match is classified by HOW the case locus normalized (see engine.normalize).
_METHOD_TO_DETAIL = {
    "snv": "canonical_snv_key",
    "mnv": "canonical_snv_key",
    "reference_left_aligned": "reference_backed_indel_key",
    "reference_free_trim": "canonical_indel_key_unaligned",
}


def _match_detail(bundle) -> str:
    """Classify a bundle's match into a report bucket.

    One of: ``variation_id`` (direct ClinVar Variation ID), ``canonical_snv_key``
    (reference-free SNV/MNV key), ``reference_backed_indel_key`` (left-aligned indel),
    ``canonical_indel_key_unaligned`` (indel keyed without a reference -> advisory),
    or ``none``.
    """
    match = bundle.match or {}
    mt = match.get("match_type")
    if mt == "variation_id":
        return "variation_id"
    if mt == "canonical_key":
        return _METHOD_TO_DETAIL.get(match.get("normalization_method"), "canonical_key")
    return "none"


def enrich_case(case: dict, provider: ClinGenEvidenceProvider) -> dict:
    """Return a deep-copied, enriched ClinVar case.

    Original fields and the expected label are preserved verbatim. Matched cases
    (by ClinVar Variation ID *or* canonical variant key) gain the ClinGen criteria
    (appended to ``signals.criteria``) and a per-case ``enrichment`` block; unmatched
    cases get an ``enrichment`` block recording the no-match so the fixture is
    self-describing. A failed normalization is recorded as such -- never as a clean
    non-match (acceptance criterion A).
    """
    out = copy.deepcopy(case)
    bundle = provider.fetch(case)
    match = bundle.match or {}
    detail = _match_detail(bundle)
    matched = detail != "none"
    by_variation_id = detail == "variation_id"

    added = 0
    if matched:
        signals = out.setdefault("signals", {})
        criteria = list(signals.get("criteria") or [])
        appended = [event_to_criterion(e) for e in bundle.events]
        signals["criteria"] = criteria + appended
        added = len(appended)

    out["enrichment"] = {
        # Back-compatible flag: True only for a direct Variation ID match.
        "clingen_variation_id_match": by_variation_id,
        "matched": matched,
        "match_type": match.get("match_type", "none"),
        "match_detail": detail,
        "clingen_case_id": match.get("clingen_case_id") if matched else None,
        "providers": [PROVIDER_NAME] if matched else [],
        "criteria_added": added,
        "normalization_failed": bool(match.get("normalized") is False),
        "warnings": list(bundle.warnings),
    }
    return out


def build_enriched(clinvar: dict, provider: ClinGenEvidenceProvider) -> dict:
    """Build the enriched benchmark dict (pure: in-memory, no file I/O)."""
    enriched_cases = [enrich_case(c, provider) for c in clinvar.get("cases", [])]

    def _count(detail: str) -> int:
        return sum(1 for c in enriched_cases if c["enrichment"]["match_detail"] == detail)

    by_variation_id = _count("variation_id")
    by_canonical_snv = _count("canonical_snv_key")
    by_reference_indel = _count("reference_backed_indel_key")
    by_canonical_indel_unaligned = _count("canonical_indel_key_unaligned")
    canonical_total = by_canonical_snv + by_reference_indel + by_canonical_indel_unaligned
    matched = sum(1 for c in enriched_cases if c["enrichment"]["matched"])
    normalization_failed = sum(
        1 for c in enriched_cases if c["enrichment"]["normalization_failed"]
    )

    criteria_added_cases = sum(1 for c in enriched_cases if c["enrichment"]["criteria_added"] > 0)
    criteria_added_total = sum(c["enrichment"]["criteria_added"] for c in enriched_cases)
    cases_with_warnings = sum(1 for c in enriched_cases if c["enrichment"]["warnings"])
    label_disagreements = sum(
        1 for c in enriched_cases if "label_disagreement" in c["enrichment"]["warnings"]
    )
    multiple_match_cases = sum(
        1 for c in enriched_cases if "multiple_clingen_matches" in c["enrichment"]["warnings"]
    )
    total = len(enriched_cases)

    # SNV/indel duplicate & mismatch rates over the ClinVar loci, before vs after
    # reference-backed normalization (reference used only if a local FASTA exists).
    loci = [locus_from_case(c) for c in clinvar.get("cases", [])]
    identity_audit = audit_loci([l for l in loci if l is not None],
                                reference=getattr(provider, "reference", None))
    identity_audit["cases_without_locus"] = sum(1 for l in loci if l is None)

    return {
        "benchmark": BENCHMARK_NAME,
        "engine_version": clinvar.get("engine_version", "1.0.0"),
        "note": (
            "REAL ClinVar benchmark ENRICHED with ClinGen ERepo criteria via ClinVar "
            "Variation ID, with a canonical variant-key fallback when no Variation ID "
            "match is available. Expected labels remain the ClinVar labels; only the "
            "input evidence is augmented. Matched cases gain the VCEP-applied ACMG "
            "criteria (with provenance); unmatched cases are unchanged and flagged. "
            "This isolates how much of the ClinVar gap was missing evidence vs. scoring."
        ),
        "source_file": "validation/fixtures/clinvar_real_v1.json + "
                       "validation/fixtures/clingen_real_v1.json",
        "enrichment_summary": {
            "source": "clinvar_real_v1 + clingen_real_v1",
            "provider": PROVIDER_NAME,
            "provider_version": PROVIDER_VERSION,
            "total_cases": total,
            # Back-compatible: direct Variation ID matches only.
            "clingen_variation_id_matches": by_variation_id,
            # New: how many cases matched by each identity route (acceptance A).
            "match_by_variation_id": by_variation_id,
            "match_by_canonical_snv_key": by_canonical_snv,
            "match_by_reference_indel_key": by_reference_indel,
            "match_by_canonical_indel_key_unaligned": by_canonical_indel_unaligned,
            "canonical_key_matches": canonical_total,
            "matched_total": matched,
            "unmatched": total - matched,
            "normalization_failed": normalization_failed,
            "criteria_added_cases": criteria_added_cases,
            "criteria_added_total": criteria_added_total,
            "cases_with_warnings": cases_with_warnings,
            "label_disagreements": label_disagreements,
            "multiple_match_cases": multiple_match_cases,
            "clingen_index_size": len(provider.index),
            "clingen_canonical_index_size": len(provider.index.canonical_keys),
            "clingen_skipped_invalid_id": provider.index.skipped_invalid_id,
            "reference_backed_normalization": identity_audit["reference_available"],
            "identity_audit": identity_audit,
        },
        "cases": enriched_cases,
    }


def main(argv: list | None = None) -> int:
    if not os.path.exists(CLINVAR_FIXTURE):
        raise SystemExit(f"Missing {CLINVAR_FIXTURE}. Build clinvar_real_v1 first.")
    if not os.path.exists(CLINGEN_FIXTURE):
        raise SystemExit(f"Missing {CLINGEN_FIXTURE}. Build clingen_real_v1 first.")

    with open(CLINVAR_FIXTURE, encoding="utf-8") as f:
        clinvar = json.load(f)

    # Discover a local GRCh38 FASTA (env or default cache path) for reference-backed
    # indel canonical-key matching; None when absent -> indels are flagged, not guessed.
    reference = load_default_reference()
    provider = ClinGenEvidenceProvider.from_fixture(CLINGEN_FIXTURE, reference=reference)
    enriched = build_enriched(clinvar, provider)

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(enriched, f, indent=2)
        f.write("\n")

    s = enriched["enrichment_summary"]
    a = s["identity_audit"]
    rel = os.path.relpath(OUT, ROOT)
    print(f"Wrote {s['total_cases']} enriched ClinVar cases -> {rel}")
    print(f"  ClinGen Variation ID index size:     {s['clingen_index_size']}")
    print(f"  ClinGen canonical-key index size:    {s['clingen_canonical_index_size']}")
    print(f"  ClinGen rows skipped (no valid VID):  {s['clingen_skipped_invalid_id']}")
    print(f"  matched by Variation ID:              {s['match_by_variation_id']}")
    print(f"  matched by canonical SNV key:         {s['match_by_canonical_snv_key']}")
    print(f"  matched by reference-backed indel key:{s['match_by_reference_indel_key']}")
    print(f"  matched by indel key (unaligned):     {s['match_by_canonical_indel_key_unaligned']}")
    print(f"  matched (total):                      {s['matched_total']}")
    print(f"  unmatched:                            {s['unmatched']}")
    print(f"  normalization failed:                 {s['normalization_failed']}")
    print(f"  criteria added (total):               {s['criteria_added_total']}")
    print(f"  cases with warnings:                  {s['cases_with_warnings']}")
    print(f"  identity audit: SNV={a['snv']} indel={a['indel']} "
          f"reference_backed={a['reference_available']}")
    print(f"    reference-free SNV collisions:      {a['reference_free']['snv']['duplicated_loci']}")
    print(f"    reference-free indel collisions:    {a['reference_free']['indel']['duplicated_loci']}")
    if a.get("reference_backed"):
        rb = a["reference_backed"]
        print(f"    reference-backed indel collisions:  {rb['indel']['duplicated_loci']} "
              f"(revealed {rb['indel_duplicates_revealed']})")
        print(f"    reference mismatches:               {rb['reference_mismatch']}")
    else:
        print(f"    indels not left-aligned (no FASTA): {a.get('indel_not_left_aligned')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
