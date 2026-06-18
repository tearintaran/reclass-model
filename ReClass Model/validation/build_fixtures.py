"""Synthetic, rule-derived validation benchmark builder (spec 12).

Writes `fixtures/synthetic_v1.json`: a deterministic, rule-derived benchmark that
exercises the harness end-to-end. The cases are CONSTRUCTED from the scoring rules
(not real clinical data), so the concordance they produce validates the *harness*,
not the clinic. Swap the ClinGen/ClinVar expert-panel set into the same schema for
the real, reportable number.

The African subgroup intentionally carries both near-miss cases (expected Likely
Pathogenic, scored at the top of the VUS band) so that ancestry stratification
surfaces a weaker cohort even when the aggregate gate passes.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BENCHMARK_NAME = "synthetic_v1"
ENGINE_VERSION = "1.0.0"


def _p(criterion: str, strength: str) -> dict:
    return {"criterion": criterion, "direction": "pathogenic", "strength": strength}


def _b(criterion: str, strength: str) -> dict:
    return {"criterion": criterion, "direction": "benign", "strength": strength}


# id, gene, ancestry, expected tier, raw signals fed to the engine.
# Point math (ClinGen SVI Bayesian points): supporting=1, moderate=2, strong=4, very_strong=8.
CASES = [
    # --- Pathogenic (>= 10) ---
    {"id": "P-01", "gene": "BRCA1", "ancestry": "European", "expected": "Pathogenic",
     "signals": {"gnomad_af": 1e-6, "criteria": [_p("PVS1", "very_strong"), _p("PS1", "strong")]}},   # 8+4+1=13
    {"id": "P-02", "gene": "MLH1", "ancestry": "African", "expected": "Pathogenic",
     "signals": {"criteria": [_p("PVS1", "very_strong"), _p("PS1", "strong"), _p("PM2", "supporting")]}},  # 13
    {"id": "P-03", "gene": "RYR1", "ancestry": "European", "expected": "Pathogenic",
     "signals": {"criteria": [_p("PVS1", "very_strong"), _p("PS1", "strong"), _p("PM2", "supporting")]}},  # 13
    {"id": "P-04", "gene": "COL1A1", "ancestry": "Latino", "expected": "Pathogenic",
     "signals": {"criteria": [_p("PVS1", "very_strong"), _p("PM1", "moderate"), _p("PM2", "supporting")]}},  # 11

    # --- Likely Pathogenic (6..9) ---
    {"id": "LP-01", "gene": "MYH7", "ancestry": "European", "expected": "Likely Pathogenic",
     "signals": {"revel": 0.70, "criteria": [_p("PVS1", "very_strong")]}},  # 8 + PP3 supporting(1) = 9
    {"id": "LP-02", "gene": "TP53", "ancestry": "East Asian", "expected": "Likely Pathogenic",
     "signals": {"criteria": [_p("PS1", "strong"), _p("PM1", "moderate"), _p("PP3", "supporting")]}},  # 7
    {"id": "LP-03", "gene": "SCN5A", "ancestry": "Latino", "expected": "Likely Pathogenic",
     "signals": {"criteria": [_p("PS1", "strong"), _p("PM1", "moderate")]}},  # 6
    {"id": "LP-04", "gene": "KCNQ1", "ancestry": "South Asian", "expected": "Likely Pathogenic",
     "signals": {"criteria": [_p("PS1", "strong"), _p("PM1", "moderate")]}},  # 6
    {"id": "LP-05", "gene": "LDLR", "ancestry": "African", "expected": "Likely Pathogenic",
     "signals": {"criteria": [_p("PVS1", "very_strong"), _p("PP3", "supporting")]}},  # 9
    {"id": "LP-06", "gene": "PKP2", "ancestry": "East Asian", "expected": "Likely Pathogenic",
     "signals": {"criteria": [_p("PS1", "strong"), _p("PM1", "moderate")]}},  # 6

    # --- Benign (<= -7) ---
    {"id": "B-01", "gene": "AF_COMMON1", "ancestry": "European", "expected": "Benign",
     "signals": {"gnomad_af": 0.08}},  # BA1 stand-alone override -> Benign, -8
    {"id": "B-02", "gene": "AF_COMMON2", "ancestry": "South Asian", "expected": "Benign",
     "signals": {"gnomad_af": 0.06}},  # BA1 stand-alone override -> Benign, -8
    {"id": "B-03", "gene": "RARE2", "ancestry": "European", "expected": "Benign",
     "signals": {"criteria": [_b("BS1", "strong"), _b("BS2", "strong")]}},  # -8
    {"id": "B-04", "gene": "POLY1", "ancestry": "East Asian", "expected": "Benign",
     "signals": {"criteria": [_b("BS1", "strong"), _b("BS2", "strong")]}},  # -8
    {"id": "B-05", "gene": "POLY2", "ancestry": "South Asian", "expected": "Benign",
     "signals": {"criteria": [_b("BS1", "strong"), _b("BS2", "strong")]}},  # -8

    # --- Likely Benign (-6..-1) ---
    {"id": "LB-01", "gene": "RARE1", "ancestry": "South Asian", "expected": "Likely Benign",
     "signals": {"criteria": [_b("BS1", "strong"), _b("BP1", "moderate")]}},  # -6
    {"id": "LB-02", "gene": "RARE3", "ancestry": "Latino", "expected": "Likely Benign",
     "signals": {"criteria": [_b("BS1", "strong"), _b("BP1", "moderate")]}},  # -6
    {"id": "LB-03", "gene": "XLB4", "ancestry": "European", "expected": "Likely Benign",
     "signals": {"criteria": [_b("BS1", "strong"), _b("BP4", "supporting")]}},  # -5
    {"id": "LB-04", "gene": "XLB5", "ancestry": "African", "expected": "Likely Benign",
     "signals": {"criteria": [_b("BS1", "strong"), _b("BP4", "supporting")]}},  # -5

    # --- VUS (0..5) ---
    {"id": "VUS-01", "gene": "UNK1", "ancestry": "European", "expected": "VUS",
     "signals": {"criteria": []}},  # 0
    {"id": "VUS-02", "gene": "UNK2", "ancestry": "African", "expected": "VUS",
     "signals": {"criteria": [_p("PM1", "moderate")]}},  # 2
    {"id": "VUS-03", "gene": "UNK3", "ancestry": "East Asian", "expected": "VUS",
     "signals": {"criteria": [_p("PS1", "strong"), _p("PP3", "supporting")]}},  # 5
    {"id": "VUS-04", "gene": "UNK4", "ancestry": "Latino", "expected": "VUS",
     "signals": {"criteria": []}},  # 0

    # --- Near-miss: expected Likely Pathogenic, scored at the top of the VUS band ---
    {"id": "NM-01", "gene": "NF1", "ancestry": "African", "expected": "Likely Pathogenic",
     "signals": {"criteria": [_p("PS1", "strong"), _p("PP3", "supporting")]}},  # 5 -> VUS (near miss)
    {"id": "NM-02", "gene": "DSP", "ancestry": "African", "expected": "Likely Pathogenic",
     "signals": {"criteria": [_p("PS1", "strong")]}},  # 4 -> VUS (near miss)
]


def _with_strata(case: dict) -> dict:
    """Attach the distinct population/VCEP fields (job1 task 5).

    The synthetic ``ancestry`` values are genuine genetic-ancestry groups (European,
    African, ...), so ``population`` mirrors ``ancestry`` here and ``vcep_group`` is
    None (this benchmark has no expert-panel grouping). ``ancestry`` is kept for the
    existing harness.
    """
    out = dict(case)
    out.setdefault("population", case.get("ancestry"))
    out.setdefault("vcep_group", None)
    return out


def build() -> dict:
    return {
        "benchmark": BENCHMARK_NAME,
        "engine_version": ENGINE_VERSION,
        "note": ("Synthetic, rule-derived benchmark for harness validation only. "
                 "Not a clinical concordance figure."),
        "field_semantics": {
            "population": "True genetic-ancestry / population-stratification group "
                          "(mirrors `ancestry` here; these are real ancestries).",
            "vcep_group": "ClinGen VCEP / expert-panel grouping (None for synthetic).",
            "ancestry": "Back-compatible alias retained for the existing harness.",
        },
        "cases": [_with_strata(c) for c in CASES],
    }


def main() -> None:
    here = os.path.dirname(os.path.abspath(__file__))
    fixtures_dir = os.path.join(here, "fixtures")
    os.makedirs(fixtures_dir, exist_ok=True)
    out_path = os.path.join(fixtures_dir, BENCHMARK_NAME + ".json")
    with open(out_path, "w") as f:
        json.dump(build(), f, indent=2)
        f.write("\n")
    print(f"Wrote {len(CASES)} cases -> {out_path}")


if __name__ == "__main__":
    main()
