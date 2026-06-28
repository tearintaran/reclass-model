# Release review — validation, calibration, and reanalysis reports

This document defines which reports are part of **routine release review** when
the ReClass engine, evidence providers, or operational runtime changes, and how
they are regenerated and signed off.

## Release triggers

Regenerate and review reports when any of the following change:

- Engine version (`engine/config.py` / `ENGINE_VERSION`)
- Scoring configuration (`engine/configs/base_v1.json` — clinical sign-off required separately)
- Evidence provider versions (REVEL, gnomAD, ClinGen fixtures)
- Normalization/reference pipeline (GRCh38 FASTA, identity audit)
- Operational runtime (`ops/scheduler.py`, queue semantics, alert lifecycle)
- Case worklist/RBAC/PHI boundary (`worklist/`, `api/routers/worklist.py`,
  `storage/worklist.py`, migration `006`)

## Routine release review set

### Tier 1 — required every release

| Report | Path / command | Gate | Reviewer |
|---|---|---|---|
| Unit/integration suite | `python -m unittest discover -s tests -v` | All tests pass | Engineering |
| Synthetic validation | `python validation/harness.py synthetic_v1` | `gate_pass: true` | Engineering + clinical QA |
| ClinGen real validation | `python validation/harness.py clingen_real_v1` | `gate_pass: true` | Clinical genetics lead |
| Pre-registered held-out evaluation | `python validation/holdout_eval.py` | Primary hypothesis passes under pinned config/partitions | Clinical genetics lead + biostatistics |

Generated artifacts:

- `validation/reports/validation_report.json` (synthetic default)
- `validation/reports/validation_report_clingen_real_v1.json`
- `validation/reports/holdout_evaluation.json`
- `validation/reports/holdout_evaluation.md`

### Tier 2 — required when evidence or fixtures change

| Report | Path / command | Purpose |
|---|---|---|
| ClinVar raw validation | `python validation/harness.py clinvar_real_v1` | Baseline concordance on sparse public evidence |
| ClinVar enriched validation | `python validation/harness.py clinvar_enriched_v1` | Concordance after ClinGen criteria enrichment |
| Before/after comparison | `python validation/compare_reports.py clinvar_real_v1 clinvar_enriched_v1` | Quantify enrichment lift |
| Failure analysis | `python validation/analyze_failures.py clinvar_enriched_v1` | Categorize discordant cases |

Generated artifacts:

- `validation/reports/validation_report_clinvar_*.json`
- `validation/reports/comparison_*.json`
- `validation/reports/failure_analysis_*.json`

**Note:** ClinVar benchmarks may fail the numeric gate while still providing
valuable regression signal. Record metrics and trend; do not block release solely
on ClinVar gate unless local policy requires it.

### Tier 3 — required when scoring thresholds or tier boundaries change

| Report | Path / command | Purpose |
|---|---|---|
| Calibration (ClinGen) | `python validation/calibration.py clingen_real_v1` | Tier distribution vs expert labels |
| Calibration (ClinVar enriched) | `python validation/calibration.py clinvar_enriched_v1` | Calibration on enriched real-data fixture |

Generated artifacts:

- `validation/reports/calibration_clingen_real_v1.json`
- `validation/reports/calibration_clinvar_enriched_v1.json`

### Tier 4 — operational (reanalysis runtime)

| Report | Source | Purpose |
|---|---|---|
| Reanalysis run report | `ops.run_report.RunReport` / `clinical.reanalysis_run` | Per-run checked/unchanged/crossed/failed/skipped counts |
| Queue health | `clinical.reanalysis_queue` state counts | Pending/failed item backlog |

Operational sign-off: operator confirms a smoke reanalysis run completes with
expected bucket counts and no unexpected `failed` reason codes.

## Regeneration procedure

From `ReClass Model/`:

```bash
PY="../.venv/bin/python"

# Tier 1
$PY -m unittest discover -s tests -v
$PY validation/harness.py synthetic_v1
$PY validation/harness.py clingen_real_v1
$PY validation/holdout_eval.py

# Tier 2 (when fixtures/evidence changed)
$PY validation/harness.py clinvar_real_v1
$PY validation/harness.py clinvar_enriched_v1
$PY validation/compare_reports.py clinvar_real_v1 clinvar_enriched_v1
$PY validation/analyze_failures.py clinvar_enriched_v1

# Tier 3 (when thresholds changed)
$PY validation/calibration.py clingen_real_v1
$PY validation/calibration.py clinvar_enriched_v1
```

Archive regenerated reports with the release record or change ticket. In this repo,
`validation/reports/` is treated as a regenerable local artifact by the data
governance policy unless a future policy explicitly changes that.

## Operational sign-off checklist

Before promoting a release to staging/production:

- [ ] Tier 1 reports regenerated within the release branch
- [ ] Tier 2/3 reports regenerated if applicable triggers fired
- [ ] Engine version and config reconstruction hash recorded in release notes
- [ ] Provider versions and fixture checksums documented
- [ ] Smoke test: API health + classify + persist + sign-off + alert lifecycle
- [ ] Worklist smoke test: create/list/assign/transition/link classification;
      verify default PHI redaction and audited authorized PHI read
- [ ] Reanalysis smoke run recorded in `clinical.reanalysis_run` (staging)
- [ ] Clinical sign-off on config changes (separate from this operational review)

## Sign-off record

Maintain a release log (LIMS, change ticket, or archived markdown addendum) with:

- Release version / git tag
- Regeneration commands run
- Pass/fail per gate with reviewer name and date
- Known exceptions and mitigations

## Out of scope for routine review

These are tracked separately:

- Identity audit reports (`validation/reports/identity_audit_*.md`) — reference data job
- Data governance refresh (`docs/data_governance.md`) — source license review
- Clinical config sign-off (`base_v1.json`) — clinical governance gate (roadmap Phase 1)
