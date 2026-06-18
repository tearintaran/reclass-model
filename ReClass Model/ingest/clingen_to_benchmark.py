"""Build a REAL validation benchmark from the ClinGen Evidence Repository (ERepo).

ERepo is an FDA-recognized, public database of expert-panel (VCEP) variant
classifications. Each record carries the panel's final ACMG/AMP assertion AND the
exact evidence codes the panel applied (e.g. `PM2, PS3, PP4_Moderate`). Those codes
map directly onto this engine's `criteria` signal, so the resulting benchmark tests
a precise, defensible question:

    Given the *same* ACMG criteria an expert panel applied, does this engine's
    deterministic point-sum (Tavtigian 2020) reproduce the panel's final tier?

This is a true concordance figure against expert calls -- unlike the synthetic
fixture, which only exercises the harness plumbing.

Input : data/raw/clingen_erepo.tsv   (downloaded from the ERepo bulk API)
Output: validation/fixtures/clingen_real_v1.json   (engine fixture schema)

Run:  python3 ingest/clingen_to_benchmark.py
"""

from __future__ import annotations

import csv
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from ingest.hgvs import (  # noqa: E402
    locus_from_hgvs_list,
    pick_coding_hgvs,
    pick_grch38_genomic_hgvs,
)

RAW = os.path.join(ROOT, "data", "raw", "clingen_erepo.tsv")
OUT = os.path.join(ROOT, "validation", "fixtures", "clingen_real_v1.json")

# ERepo "Assertion" text -> engine tier.
ASSERTION_TO_TIER = {
    "pathogenic": "Pathogenic",
    "likely pathogenic": "Likely Pathogenic",
    "uncertain significance": "VUS",
    "likely benign": "Likely Benign",
    "benign": "Benign",
}

# Base ACMG code prefix -> (direction, default strength) per ACMG/AMP 2015.
PREFIX_RULES = [
    ("PVS", ("pathogenic", "very_strong")),
    ("PS", ("pathogenic", "strong")),
    ("PM", ("pathogenic", "moderate")),
    ("PP", ("pathogenic", "supporting")),
    ("BA", ("benign", "stand_alone")),
    ("BS", ("benign", "strong")),
    ("BP", ("benign", "supporting")),
]

# Strength-modifier suffix (ClinGen SVI) -> engine strength key.
SUFFIX_TO_STRENGTH = {
    "verystrong": "very_strong",
    "very_strong": "very_strong",
    "strong": "strong",
    "moderate": "moderate",
    "supporting": "supporting",
    "standalone": "stand_alone",
    "stand_alone": "stand_alone",
}


def parse_evidence_code(code: str) -> dict | None:
    """Turn one applied evidence code (e.g. 'PP4_Moderate', 'PM2') into a criterion.

    Returns {criterion, direction, strength} matching the engine's `criteria`
    signal, or None if the token is unrecognized.
    """
    code = code.strip()
    if not code:
        return None

    base, _, suffix = code.partition("_")
    base = base.strip().upper()

    direction = strength = None
    for prefix, (d, default_strength) in PREFIX_RULES:
        if base.startswith(prefix):
            direction, strength = d, default_strength
            break
    if direction is None:
        return None  # not an ACMG P*/B* code (e.g. a free-text note)

    if suffix:
        strength = SUFFIX_TO_STRENGTH.get(suffix.strip().lower(), strength)

    return {"criterion": base, "direction": direction, "strength": strength,
            "source": "clingen", "version": "ERepo"}


def parse_applied_codes(cell: str) -> list[dict]:
    """Parse the comma-separated 'Applied Evidence Codes (Met)' cell."""
    out = []
    for token in cell.split(","):
        crit = parse_evidence_code(token)
        if crit is not None:
            out.append(crit)
    return out


def main() -> None:
    if not os.path.exists(RAW):
        raise SystemExit(f"Missing {RAW}. Download the ERepo bulk TSV first.")

    cases = []
    skipped_assertion: dict[str, int] = {}
    skipped_no_codes = 0
    retracted = 0
    with_locus = 0
    with_indel_hgvs = 0
    with_transcript = 0

    # ERepo free-text fields ('Summary of interpretation') contain stray double
    # quotes; quote-aware CSV silently merges rows on them. The export is strictly
    # one record per physical line with tab delimiters, so parse with QUOTE_NONE.
    with open(RAW, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter="\t", quoting=csv.QUOTE_NONE)
        malformed = 0
        for i, row in enumerate(reader):
            if None in row or len(row) != 20:
                malformed += 1
                continue
            if (row.get("Retracted") or "").strip().lower() == "true":
                retracted += 1
                continue

            assertion = (row.get("Assertion") or "").strip().lower()
            tier = ASSERTION_TO_TIER.get(assertion)
            if tier is None:
                skipped_assertion[assertion] = skipped_assertion.get(assertion, 0) + 1
                continue

            criteria = parse_applied_codes(row.get("Applied Evidence Codes (Met)") or "")
            if not criteria:
                # No machine-parseable criteria (e.g. benign-by-frequency only,
                # or codes we cannot map) -> the engine has nothing to sum.
                skipped_no_codes += 1
                continue

            gene = (row.get("HGNC Gene Symbol") or "").strip() or "NA"
            panel = (row.get("Expert Panel") or "").strip() or "Unspecified VCEP"
            cv_id = (row.get("ClinVar Variation Id") or "").strip()
            variation = (row.get("#Variation") or "").strip()

            hgvs_cell = row.get("HGVS Expressions") or ""
            # Recover a GRCh38 SNV/MNV locus from the genomic substitution HGVS so the
            # canonical-key fallback matcher has reference-free coordinates (job1 task 2/3).
            locus = locus_from_hgvs_list(hgvs_cell)
            if locus is not None:
                with_locus += 1
            # Also record the GRCh38 genomic HGVS token itself (substitution OR indel).
            # For an indel this is the only handle on its coordinates: the deleted/
            # duplicated bases need the FASTA, so the matching layer resolves the token
            # against the reference (job1 task 1, the `hgvs_g` tier). Storing the token
            # -- not the resolved locus -- keeps this ingest step reference-free and
            # deterministic from the TSV alone.
            genomic_hgvs = pick_grch38_genomic_hgvs(hgvs_cell)
            if genomic_hgvs is not None and locus is None:
                with_indel_hgvs += 1
            # Carry the transcript identity (job1 task 4): the RefSeq coding HGVS names
            # the transcript the panel interpreted against. ERepo does not flag MANE
            # Select, so the RefSeq transcript is recorded under `refseq` (+ hgvs_c);
            # the MANE Select field is left None rather than guessed.
            coding = pick_coding_hgvs(hgvs_cell)
            transcript = None
            if coding is not None:
                with_transcript += 1
                transcript = {"refseq": coding[0], "hgvs_c": coding[1],
                              "gene": gene, "source": "ClinGen ERepo"}

            case = {
                "id": f"CG-{cv_id or i}",
                "gene": gene,
                # job1 task 5: separate the field families. ERepo carries no
                # genetic-ancestry, so `population` is None; `vcep_group` holds the
                # expert-panel grouping. `ancestry` is retained as a back-compatible
                # alias of `vcep_group` for the existing harness, which buckets it as
                # a panel (not an ancestry) via grouping_kind().
                "population": None,
                "vcep_group": panel,
                "ancestry": panel,
                "expected": tier,
                "signals": {"criteria": criteria},
                "provenance": {"source": "ClinGen ERepo", "variation": variation,
                               "clinvar_id": cv_id,
                               "grch38_hgvs": genomic_hgvs},
            }
            if locus is not None:
                case["locus"] = {k: locus[k] for k in ("chrom", "pos", "ref", "alt", "snv")}
            if transcript is not None:
                case["transcript"] = transcript
            cases.append(case)

    benchmark = {
        "benchmark": "clingen_real_v1",
        "engine_version": "1.0.0",
        "note": ("REAL expert-panel benchmark from the ClinGen Evidence Repository. "
                 "Each case feeds the engine the exact ACMG criteria the VCEP applied; "
                 "concordance measures whether the deterministic point-sum reproduces "
                 "the panel's final tier. Cases also carry a GRCh38 SNV/MNV `locus` "
                 "(parsed from the genomic HGVS) plus the genomic HGVS token itself in "
                 "`provenance.grch38_hgvs` (substitution OR indel) so the canonical-key "
                 "and HGVS-genomic fallback matchers can recover ClinVar cases with no "
                 "Variation ID match -- indel tokens are resolved against the reference "
                 "in the matching layer."),
        "field_semantics": {
            "population": "True genetic-ancestry / population-stratification group. "
                          "None here: ERepo carries no per-case ancestry.",
            "vcep_group": "ClinGen VCEP / expert-panel grouping (NOT an ancestry).",
            "ancestry": "Back-compatible alias of vcep_group for the existing harness; "
                        "prefer population (ancestry) and vcep_group (panel).",
        },
        "source_file": "data/raw/clingen_erepo.tsv",
        "cases": cases,
    }

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as f:
        json.dump(benchmark, f, indent=2)
        f.write("\n")

    print(f"Wrote {len(cases)} real cases -> {OUT}")
    print(f"  with GRCh38 SNV/MNV locus:  {with_locus}")
    print(f"  with GRCh38 indel HGVS only: {with_indel_hgvs}")
    print(f"  with transcript identity:    {with_transcript}")
    print(f"  malformed rows skipped:   {malformed}")
    print(f"  retracted skipped:        {retracted}")
    print(f"  no parseable criteria:    {skipped_no_codes}")
    if skipped_assertion:
        print("  skipped assertions (non-standard tiers):")
        for a, n in sorted(skipped_assertion.items(), key=lambda x: -x[1]):
            print(f"    {n:>5}  {a!r}")


if __name__ == "__main__":
    sys.exit(main())
