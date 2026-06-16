# Standardized Variant Reclassification Engine

This folder contains a local proof of concept for a deterministic ACMG/AMP variant
reclassification engine.

The core design goal is reproducibility: the same standardized evidence, under the
same engine/configuration version, should produce the same classification and the
same reconstruction hash. The engine is decision support. It does not replace
human review or clinical sign-off.

## Current Status

Implemented and runnable now:

- Deterministic scoring engine in `engine/scoring.py`.
- Versioned scoring constants, cutoffs, frequency thresholds, REVEL bins, and
  VCEP/gene/disease overrides in `engine/config.py`,
  `engine/config_registry.py`, and `engine/configs/base_v1.json`.
- Reference-free normalization plus reference-backed left-alignment in
  `engine/normalize.py` and `engine/reference.py`.
- GRCh38 FASTA cache/status helper in `engine/reference_cache.py`.
- Evidence bundle model and reusable providers for ClinGen ERepo, REVEL, and
  gnomAD in `evidence/`.
- Canonical variant identity helpers compatible with provider keys
  (`1-100-A-G`) and storage keys (`GRCh38-1-100-A-G`).
- ClinVar enrichment from direct ClinGen Variation ID matches, with canonical-key
  fallback support when source loci are available.
- REVEL and gnomAD ingest/enrichment scripts refactored through their providers.
- Tier-crossing diff logic in `monitoring/diff.py`.
- Continuous reanalysis and calibrated cohort PS4 helpers in
  `monitoring/reanalysis.py`.
- Operational reanalysis queue, scheduler, run-report, and repo-guard helpers in
  `ops/`.
- Tenant-aware FastAPI service layer in `api/`, including classification,
  evidence-resolution, persistence, sign-off, reanalysis, alert, validation, and
  report endpoints.
- Technical reviewer and patient-safe summary report generation in `reporting/`.
- Validation harness, failure analysis, before/after comparison tool,
  calibration reports, and fixtures in `validation/`.
- Diagnostic plotting in `validation/plots.py`; plots are written to the project
  root `plots/` folder.
- Real-data ingest scripts in `ingest/`.
- PostgreSQL schema in `db/schema.sql`, apply tooling in `db/apply.py`.
- Application persistence layer in `storage/` with tenant/RLS sessions,
  classification/evidence/alert repositories, persisted evidence bundles, cohort
  counts, and reconstruction verification.
- Data-governance policy in `docs/data_governance.md`, plus `.gitignore` and
  `ops/repo_guard.py` commit hygiene.
- 389 tests passing in the current environment.

Important limitations:

- The default scoring configuration is reconstructed from documented
  ACMG/AMP/SVI assumptions and includes reviewable override examples. It still
  requires local clinical review and sign-off before any real-world use.
- A production GRCh38 FASTA is not bundled. `engine.reference_cache` reports local
  status and can point at a local FASTA, but the default file is absent unless the
  user supplies it.
- Canonical-key fallback matching is implemented, but current ClinGen ERepo
  fixtures have no loci in the canonical-key index, so the real ClinVar enrichment
  lift still comes from direct Variation ID matches.
- Cohort PS4 rules now encode published ClinGen VCEP proband-count specifications
  for the supported gene sets, with a conservative fallback elsewhere. They still
  require current-spec review, local clinical sign-off, and separately supplied PM2
  evidence.
- The API and report/sign-off workflow are implemented as service surfaces, but
  there is no clinician-facing frontend and nothing here is a production clinical
  deployment.

## Quick Start

Run from this folder:

```bash
cd "/Users/taranramadoss/Documents/Projects/First Project/ReClass Model"

../.venv/bin/python -m unittest discover -s tests -v
../.venv/bin/python validation/harness.py
../.venv/bin/python validation/harness.py clingen_real_v1
../.venv/bin/python validation/harness.py clinvar_real_v1
../.venv/bin/python evidence/enrich_clinvar.py
../.venv/bin/python validation/harness.py clinvar_enriched_v1
../.venv/bin/python validation/analyze_failures.py clinvar_enriched_v1
../.venv/bin/python validation/compare_reports.py clinvar_real_v1 clinvar_enriched_v1
../.venv/bin/python validation/calibration.py clingen_real_v1
../.venv/bin/python -m engine.reference_cache --status
```

Expected current outcomes:

| Command | Expected result |
|---|---|
| Unit/integration tests | 389 tests pass in the current environment |
| `validation/harness.py` | Synthetic gate PASS |
| `validation/harness.py clingen_real_v1` | Real ClinGen gate PASS |
| `validation/harness.py clinvar_real_v1` | Raw ClinVar gate FAIL, exposing sparse evidence |
| `validation/harness.py clinvar_enriched_v1` | Enriched ClinVar gate FAIL, but substantially improved over raw ClinVar |
| `validation/compare_reports.py clinvar_real_v1 clinvar_enriched_v1` | Before/after report showing improvement from ClinGen evidence |
| `validation/calibration.py clingen_real_v1` | Writes VCEP/gene/disease calibration triage |

## Validation Baselines

| Benchmark | Cases | Gate | Definitive concordance | Serious discordance | Overall exact concordance |
|---|---:|---|---:|---:|---:|
| `synthetic_v1` | 25 | PASS | 90.5% | 0 | 92.0% |
| `clingen_real_v1` | 12,446 | PASS | 94.7% | 4 | 93.0% |
| `clinvar_real_v1` | 21,638 | FAIL | 5.0% | 34 | 19.9% |
| `clinvar_enriched_v1` | 21,638 | FAIL | 37.8% | 9 | 43.3% |

`clingen_real_v1` feeds the engine ACMG criteria that expert panels applied and
therefore tests whether the point model reproduces panel classifications.

`clinvar_real_v1` is intentionally sparse: high-confidence ClinVar labels plus
available REVEL/frequency signals. Its failure shows that sparse public signals do
not reproduce most expert pathogenic calls.

`clinvar_enriched_v1` preserves the ClinVar expected labels but adds matched
ClinGen-applied criteria for direct ClinVar Variation ID matches. The improvement
shows that evidence completeness is the main blocker; the remaining failure shows
that much evidence is still missing.

## Evidence Providers

Reusable providers live in `evidence/`:

```text
evidence/
  model.py
  providers.py
  clingen.py
  revel.py
  gnomad.py
  enrich_clinvar.py
```

Provider behavior:

- `ClinGenEvidenceProvider` joins ClinVar records to ClinGen ERepo by direct
  ClinVar Variation ID first, then falls back to canonical-key matching when
  source loci are available. It returns expert-applied criteria plus provenance
  and match-route details.
- `RevelProvider` looks up REVEL scores by `(chrom, grch38_pos, ref, alt)` from a
  local cache/index and emits PP3/BP4 events when thresholds apply.
- `GnomadProvider` uses targeted gnomAD v4 responses or a local cache, requests
  `joint.faf95.popmax`, falls back to genome/exome AF, and emits BA1/BS1/PM2
  events when thresholds apply.

All providers return an `EvidenceBundle` with events, provider versions, source
records, warnings, and match details. gnomAD absence is represented as unknown
evidence, not allele frequency zero.

Provider caches live under:

```text
data/cache/providers/
```

## ClinVar Enrichment

Current ClinGen enrichment summary:

- ClinVar cases: 21,638
- Direct ClinGen Variation ID matches: 10,649
- Canonical SNV key matches: 0
- Reference-backed indel key matches: 0
- Unmatched cases: 10,989
- Normalization failures: 2
- Cases gaining criteria: 10,649
- Total criteria added: 33,094
- Cases with warnings: 11,021
- Label disagreements among matched records: 30
- Multiple ClinGen match cases resolved deterministically: 2

Canonical-key support is active, but the current ERepo-derived fixture has no
usable locus fields for the ClinGen canonical-key index, so real-data fallback
matches are currently zero.

The generated enriched fixture is:

```text
validation/fixtures/clinvar_enriched_v1.json
```

## Storage And Reanalysis

The PostgreSQL layer separates identified clinical data from de-identified
research evidence:

- `clinical.*`: tenant, patient, variant, classification, alert, and reanalysis
  rows protected by row-level security.
- `research.*`: de-identified variant records, evidence events, evidence bundles,
  source records, and cohort counts.

The storage layer can:

- Persist classification receipts.
- Persist standardized evidence events.
- Persist full `EvidenceBundle` provenance.
- Reconstruct and verify classifications from stored evidence.
- Detect tampering with receipts or bundle provenance.
- Store cohort counts for PS4-style evidence.
- Queue variants for reanalysis by provider-version, evidence, or config-version
  trigger.
- Record same-tier reanalysis events without paging.
- Create alerts only on tier crossings.
- Persist reanalysis run reports and link old/new evidence-bundle receipts when
  available.

Storage/reanalysis tests require PostgreSQL 16 and skip cleanly when a server is
not available.

## Actual Repository Layout

```text
ReClass Model/
  README.md
  manifest.md
  00-overview.md
  00-orchestrator-agent.md
  requirements.txt
  validation_report.md
  scoring.py
  engine/
    config.py
    config_registry.py
    configs/
    normalize.py
    reference.py
    reference_cache.py
    scoring.py
  evidence/
    model.py
    providers.py
    clingen.py
    revel.py
    gnomad.py
    enrich_clinvar.py
  monitoring/
    diff.py
    reanalysis.py
  ops/
    queue.py
    scheduler.py
    run_report.py
    repo_guard.py
  api/
    app.py
    routers/
    schemas.py
    store.py
  reporting/
    reviewer.py
    summary.py
    render.py
  validation/
    build_fixtures.py
    analyze_failures.py
    compare_reports.py
    plots.py
    harness.py
    fixtures/
    reports/
  ingest/
    README.md
    clingen_to_benchmark.py
    clinvar_to_benchmark.py
    enrich_revel.py
    enrich_gnomad.py
  storage/
    db.py
    classifications.py
    evidence.py
    alerts.py
    verify.py
  db/
    schema.sql
    apply.py
  docs/
    data_governance.md
  tests/
  data/
    raw/
    cache/providers/
    reference/
```

A sibling `plots/` folder is created at the project root for PNG diagnostics.

Notes:

- Prefer `from engine.scoring import ...`; top-level `scoring.py` is a
  compatibility copy.
- `data/raw/` contains large local data files and should be treated as a local data
  cache unless a future repo policy says otherwise.
- `data/cache/providers/` stores provider caches and generated enrichment copies.
- `data/reference/` is for local FASTA cache files; large FASTA/FAI files should
  not be committed.
- `docs/data_governance.md` records external source versions, licenses,
  committed-vs-regenerated file policy, and reproducibility steps.
- Generated validation reports are artifacts, not source of business logic.

## Historical Agent Design

The original design described a 14-module specialist-agent workflow. This local
snapshot does not contain the full generated `docs/specs/` tree, specialist
definition files, `orchestration/` directory, or `generate_project.py`.

The surviving docs are still useful as architecture notes:

| Module area | Current status |
|---|---|
| System overview | Present as `00-overview.md`, `../overview.md`, and this README |
| Architecture | Not implemented as a service topology yet |
| Data model/schema | SQL schema, apply tooling, storage layer, RLS, evidence-bundle, cohort-count, and reanalysis tests implemented |
| Ingestion/normalization | Canonical identity, reference-free normalization, reference-backed left-alignment, reference-cache status helper, and real-data ingest scripts implemented |
| Scoring | Implemented and tested with file-backed versioned config and VCEP/gene/disease override support |
| Evidence integration | ClinGen, REVEL, and gnomAD providers implemented with direct-ID and canonical-key match accounting |
| Monitoring | Tier-crossing diff, reanalysis helpers, operational queue/scheduler/run reports, and alert lifecycle implemented and tested |
| Cohort PS4 | Cohort-count PS4 helpers calibrated to published ClinGen VCEP proband-count specifications for supported gene sets |
| Reporting/sign-off | Technical reviewer reports, patient-safe summaries, draft/released status, and credentialed sign-off workflow implemented |
| API | Tenant-aware FastAPI layer implemented |
| Frontend | Remaining product work |
| Security/privacy | SQL RLS plus tenant-isolation, research-boundary, reconstruction, data-governance, and repo-guard tests implemented |
| Validation | Harness, failure analysis, comparison reports, calibration reports, plots, evidence-aware summaries, and generated reports exist |
| Roadmap | `../gap.md` lists unfinished todos only |

## Core Design Commitments

- **Pure scoring core:** `classify()` has no I/O, network access, randomness, or
  wall-clock dependency.
- **Auditable evidence:** each contribution records source, criterion, direction,
  strength, points, and source version.
- **Reconstruction:** each result includes an engine version and SHA-256
  reconstruction hash.
- **Provider provenance:** evidence bundles preserve source records, provider
  versions, warnings, and match details.
- **Decision support:** no classification should be released clinically without
  credentialed human sign-off.
- **Data boundary:** identified clinical data and de-identified research data are
  separated in the schema.

## What To Build Next

Read `../gap.md` for the unfinished todo list.

The highest-value next work is now clinical/product hardening rather than missing
core repo modules: install a real GRCh38 FASTA, refresh source snapshots under the
governance policy, expand evidence providers beyond the current public-data slice,
validate the config/PS4 rules with credentialed reviewers, and build any desired
clinician-facing frontend on top of the existing API.
