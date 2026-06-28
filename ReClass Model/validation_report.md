# Validation Summary

This is the human-readable validation summary for the current local project. The
generated reports live in `validation/reports/`; the JSON files carry exact
per-run metrics, while the Markdown reports are rounded for readability.

Run from `ReClass Model/`:

```bash
../.venv/bin/python validation/harness.py
../.venv/bin/python validation/harness.py clingen_real_v1
../.venv/bin/python validation/harness.py clinvar_real_v1
../.venv/bin/python validation/harness.py clinvar_enriched_v1
../.venv/bin/python validation/compare_reports.py clinvar_real_v1 clinvar_enriched_v1
../.venv/bin/python validation/holdout_eval.py
```

> **Single-application correction (2026-06-24).** The evidence-integration layer was
> corrected to enforce ACMG/AMP single-application: a criterion supplied by both an
> expert ClinGen curation and a computational mapper (e.g. PP3 from ClinGen *and*
> REVEL) is now scored **once** (expert strength preferred, else the strongest),
> instead of being summed twice. This fixes a tier-flipping defect that affected
> 4,733 of the 21,638 enriched-ClinVar cases. The scoring **config** (point values,
> tier cut-points, frequency thresholds, overrides) is unchanged, so the locked
> `config_hash` and `engine_version` still hold and the **H1 ClinGen primary gate is
> unaffected**. The enriched-ClinVar numbers below are the corrected values — the
> correction *raised* enriched concordance (the double-count was over-shooting tiers).
> See [engine/scoring.py](engine/scoring.py) `collapse_single_application`.

## Current benchmark summary

| Benchmark | Cases | Gate | Definitive concordance | Serious discordance | Overall exact concordance | Meaning |
|---|---:|---|---:|---:|---:|---|
| `synthetic_v1` | 32 | PASS | 92.9% | 0 cases | 93.8% | Harness/scoring plumbing works |
| `clingen_real_v1` | 12,446 | PASS | 94.7% | 4 cases | 93.0% | Complete expert-applied criteria reproduce VCEP calls well |
| `clinvar_real_v1` | 21,638 | FAIL | 5.0% | 34 cases | 19.9% | Partial ClinVar evidence exposes the evidence-integration gap |
| `clinvar_enriched_v1` | 21,638 | FAIL | 47.1% | 7 cases | 54.4% | Direct and fallback ClinGen criteria matches improve ClinVar but do not solve missing evidence |

## Pre-registered held-out evaluation

The locked 30% holdout is keyed on GRCh38 variant identity, shared across
fixtures, blind to expected labels, and inaccessible to calibration. The
pre-registration, config hash, split rule, partition fingerprints, and acceptance
bars are recorded in `validation/preregistration.md` and
`validation/preregistration.json`.

| Held-out benchmark | Holdout n | Definitive concordance (95% CI) | Serious discordance | Development concordance |
|---|---:|---:|---:|---:|
| `clingen_real_v1` | 3,635 | 95.4% (94.5–96.1%) | 2 (0.1%) | 94.4% |
| `clinvar_real_v1` | 6,487 | 5.1% (4.5–5.7%) | 13 | 5.0% |
| `clinvar_enriched_v1` | 6,487 | 49.1% (47.8–50.5%) | 6 | 46.2% |

The primary ClinGen hypothesis passes: the concordance lower bound exceeds 85%
and the serious-discordance upper bound remains below 1%. Enrichment adds 44.0
percentage points on the same held-out ClinVar variants.

> **Blinding scope (honest framing).** Leakage control is unconditional (locked,
> hash-pinned config; label-blind reserved holdout the calibrator cannot load). But
> only the **H1** gating bars (0.85 / <0.01) are a-priori — committed in `harness.py`
> at the project's initial commit. The **H3 ≥15 pp contrast** and **3 pp overfit**
> thresholds are *descriptive*: they were set with population-level full-fixture
> results (including the then-reported +37.4 pp enrichment lift) already computed and
> committed. See `validation/preregistration.md` § *Disclosure*. (The single-application
> correction above later revised the lift upward, so the descriptive bars clear by an
> even wider margin.)

## Raw ClinVar vs enriched ClinVar

`clinvar_enriched_v1` preserves ClinVar expected labels but adds ClinGen ERepo
criteria for direct ClinVar Variation ID matches, canonical SNV-key fallback
matches, and genomic-HGVS fallback matches when source identity fields are
available.

| Measure | Raw ClinVar | Enriched ClinVar | Change |
|---|---:|---:|---:|
| Cases | 21,638 | 21,638 | 0 |
| Cases with structured criteria | 0 | 11,970 | +11,970 |
| Definitive concordance | 5.0% | 47.1% | +42.1 percentage points |
| Overall exact concordance | 19.9% | 54.4% | +34.5 percentage points |
| Serious discordance count | 34 | 7 | -27 |
| Improved cases | n/a | 8,041 | n/a |
| Worsened cases | n/a | 321 | n/a |

The comparison report is:

```text
validation/reports/comparison_clinvar_real_v1_vs_clinvar_enriched_v1.md
```

## Interpretation

The scoring engine is strongest when it receives complete ACMG criteria. The
ClinGen benchmark demonstrates this: expert-panel applied criteria produce high
concordance with expert-panel final tiers.

Raw ClinVar asks a harder and less complete question: whether labels plus
frequency plus REVEL are enough. They are not. Most pathogenic/likely pathogenic
ClinVar calls need additional evidence such as PVS1, PS3, PM3, PS2/PM6, PP1, PP4,
and PS4.

The enriched ClinVar benchmark proves that structured evidence recovery helps:
direct ClinGen Variation ID matches plus canonical SNV key matches (940) and
genomic-HGVS matches (381) add 37,873 criteria across 11,970 cases, raise
definitive concordance substantially (Pathogenic recall 0% to 31.1%, Likely
Pathogenic recall 0% to 85.5%), and reduce serious errors.
It still fails because 9,668 cases remain unmatched and many variants still lack the
evidence expert reviewers use; evidence completeness is the blocker.

## Generated reports

- `validation/reports/validation_report.md`
- `validation/reports/validation_report.json`
- `validation/reports/validation_report_clingen_real_v1.md`
- `validation/reports/validation_report_clingen_real_v1.json`
- `validation/reports/validation_report_clinvar_real_v1.md`
- `validation/reports/validation_report_clinvar_real_v1.json`
- `validation/reports/validation_report_clinvar_enriched_v1.md`
- `validation/reports/validation_report_clinvar_enriched_v1.json`
- `validation/reports/failure_analysis_clingen_real_v1.md`
- `validation/reports/failure_analysis_clinvar_real_v1.md`
- `validation/reports/failure_analysis_clinvar_enriched_v1.md`
- `validation/reports/comparison_clinvar_real_v1_vs_clinvar_enriched_v1.md`
- `validation/reports/holdout_evaluation.md`
- `validation/reports/holdout_evaluation.json`

Large real-data reports include confusion matrices and per-case details. They are
generated artifacts and may be regenerated by the validation harness and analysis
tools.

## Next validation improvements

- Separate true ancestry stratification from VCEP/group stratification in the
  fixture schema and reports.
- Keep reference-backed indel and canonical-key match-rate reporting current as
  source snapshots change; the current reference-backed indel lift is 0 on this
  real fixture.
- Expand calibration reports as more VCEP/gene/disease-specific rule specs are
  locally reviewed and adopted.
- Rank remaining evidence-provider work beyond ClinGen/REVEL/gnomAD by expected
  impact on current failure categories.
