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
- Evidence bundle model and reusable providers for ClinGen ERepo, REVEL, gnomAD,
  AlphaMissense, conservation, and gene-constraint context in `evidence/`.
- Canonical variant identity helpers compatible with provider keys
  (`1-100-A-G`) and storage keys (`GRCh38-1-100-A-G`).
- ClinVar enrichment from direct ClinGen Variation ID matches, with canonical-key
  and genomic-HGVS fallback support when source loci are available.
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
- PostgreSQL schema in `db/schema.sql`, migration-ledger apply tooling in
  `db/apply.py`, and backup/restore scripts under `deploy/`.
- Application persistence layer in `storage/` with tenant/RLS sessions,
  classification/evidence/alert repositories, persisted evidence bundles, cohort
  counts, and reconstruction verification.
- Data-governance policy in `docs/data_governance.md`, plus `.gitignore` and
  `ops/repo_guard.py` commit hygiene.
- Clinician reviewer frontend mounted at `/reviewer/`, wired to the API resolve,
  classify, draft, report, sign-off, and alert endpoints. It discovers providers
  from the API, keeps the bearer token in memory by default, renders structured
  views with loading/error/empty states, holds layout on a small viewport, and
  ships a dependency-free browser test harness under `frontend/tests/`.
- Operator CLI in `cli.py` with `classify`, `validate`, `reference status`,
  `compare`, `calibration`, and `report` (analytical-validation and failures)
  subcommands, each offering `--json` output where useful.
- Analytical-validation report generator in `validation/analytical_validation.py`
  and per-case serious-discordance drill-down in `validation/analyze_failures.py`.
- Upstream-evidence adapters in `evidence/upstream.py` for de novo (PS2/PM6),
  phasing (PM3/BP2), segregation (PP1/BS4), phenotype specificity (PP4), functional
  assays (PS3/BS3), disease mechanism (PP2/BP1), and case-control/PS4, each
  recording source version/checksum/access date with explicit no-call behavior.
- Reproducible source-cache builders with manifests (version/checksum/access date)
  in `evidence/cache_manifest.py`, used by the AlphaMissense, conservation,
  gene-constraint, and functional/phenotype caches.
- Identity-matching routes for ClinVar Variation ID, ClinVar Allele ID, SPDI,
  canonical SNV key, MANE/coding-HGVS transcript, and genomic HGVS, with explicit
  ambiguity accounting (`ingest/hgvs.py`, `engine/normalize.py`, `evidence/clingen.py`).
- MANE Select transcript identity and PS4 denominator/cohort counts modeled in
  `evidence/model.py` and populated through `ingest/cohort_to_evidence.py`.
- Development/validation/holdout fixture splits with an anti-leakage guardrail in
  `validation/fixture_splits.py`; reviewer review packets in `reporting/reviewer.py`;
  serious-discordance adjudication in `validation/analyze_failures.py`; configurable
  conflict-policy checks in `validation/conflict_policy.py`; and scoped validation
  gates in `validation/analytical_validation.py`.
- A pinned OpenAPI artifact (`api/openapi.json`, `api/openapi_contract.py`) with
  runnable cookbook examples (`api/cookbook_examples.py`, `docs/api_cookbook.md`),
  drift-checked in CI.
- Deterministic FHIR Genomics export with amended-report state transitions and
  replayable outbound payloads in `reporting/fhir.py`.
- Change-control reanalysis triggers (source-snapshot/provider-version/config/
  conflict-policy) that enqueue affected variants with an auditable run manifest in
  `ops/scheduler.py` and `ops/queue.py`.
- Startup/preflight production-readiness checks in `api/settings.py` covering
  required env vars, OIDC/JWKS, audit backend, DB role, reference-FASTA metadata,
  and provider-cache manifests, each failing with a named error.
- A GitHub Actions CI pipeline (`.github/workflows/ci.yml`) running PostgreSQL-backed
  tests, migration apply/restore rehearsal, Docker image build, generated
  validation-report artifacts, headless frontend checks, and optional FHIR profile
  validation.
- 781 tests passing in the current environment.

Important limitations:

- The default scoring configuration is reconstructed from documented
  ACMG/AMP/SVI assumptions and includes reviewable override examples. It still
  requires local clinical review and sign-off before any real-world use.
- A production GRCh38 FASTA is not bundled. `engine.reference_cache` reports local
  status and can point at a local FASTA; this environment has an Ensembl
  release-110 cache installed, but every deployment must supply and checksum-pin
  its own local-only reference.
- Identity matching uses a fixed route priority — Variation ID, Allele ID,
  canonical SNV key, SPDI, MANE/coding-HGVS transcript, then genomic HGVS — and
  flags ambiguous multi-record matches rather than resolving them. Canonical-key
  fallback contributes measurable real-data lift when Variation ID is absent.
- Cohort PS4 rules now encode published ClinGen Hearing Loss proband-count
  specifications for supported dominant genes and a denominator-aware
  Cardiomyopathy odds-ratio / 95% CI path, with a conservative fallback elsewhere.
  They still require current-spec review, local clinical sign-off, and separately
  supplied PM2 evidence where a VCEP requires it.
- The API, report/sign-off workflow, and reviewer frontend are implemented as
  service surfaces, but nothing here is a production clinical deployment until
  the documented operational, database, and credentialed sign-off steps are
  completed.

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
| Unit/integration tests | 781 tests pass in the current environment |
| `validation/harness.py` | Synthetic gate PASS |
| `validation/harness.py clingen_real_v1` | Real ClinGen gate PASS |
| `validation/harness.py clinvar_real_v1` | Raw ClinVar gate FAIL, exposing sparse evidence |
| `validation/harness.py clinvar_enriched_v1` | Enriched ClinVar gate FAIL, but substantially improved over raw ClinVar |
| `validation/compare_reports.py clinvar_real_v1 clinvar_enriched_v1` | Before/after report showing improvement from ClinGen evidence |
| `validation/calibration.py clingen_real_v1` | Writes VCEP/gene/disease calibration triage |

## Validation Baselines

| Benchmark | Cases | Gate | Definitive concordance | Serious discordance | Overall exact concordance |
|---|---:|---|---:|---:|---:|
| `synthetic_v1` | 32 | PASS | 92.9% | 0 | 93.8% |
| `clingen_real_v1` | 12,446 | PASS | 94.7% | 4 | 93.0% |
| `clinvar_real_v1` | 21,638 | FAIL | 5.0% | 34 | 19.9% |
| `clinvar_enriched_v1` | 21,638 | FAIL | 42.4% | 6 | 46.6% |

`clingen_real_v1` feeds the engine ACMG criteria that expert panels applied and
therefore tests whether the point model reproduces panel classifications.

`clinvar_real_v1` is intentionally sparse: high-confidence ClinVar labels plus
available REVEL/frequency signals. Its failure shows that sparse public signals do
not reproduce most expert pathogenic calls.

`clinvar_enriched_v1` preserves the ClinVar expected labels but adds matched
ClinGen-applied criteria through direct ClinVar Variation ID matches plus weaker
fallback routes. The improvement shows that evidence completeness is the main
blocker; the remaining failure shows that much evidence is still missing.

## Evidence Providers

Reusable providers live in `evidence/`:

```text
evidence/
  model.py
  providers.py
  clingen.py
  revel.py
  gnomad.py
  alphamissense.py
  computational.py
  criteria_ext.py
  upstream.py
  cache_manifest.py
  enrich_clinvar.py
```

Provider behavior:

- `ClinGenEvidenceProvider` joins ClinVar records to ClinGen ERepo by a fixed
  route priority — Variation ID, Allele ID, canonical SNV key, SPDI, MANE/coding-HGVS
  transcript, then genomic HGVS. It returns expert-applied criteria plus provenance
  and match-route details, carries MANE transcript identity into the bundle, and
  flags an ambiguous multi-record match (emitting no events) instead of resolving it.
- `RevelProvider` looks up REVEL scores by `(chrom, grch38_pos, ref, alt)` from a
  local cache/index and emits PP3/BP4 events when thresholds apply.
- `GnomadProvider` uses targeted gnomAD v4 responses or a local cache, requests
  `joint.faf95.popmax`, falls back to genome/exome AF, and emits BA1/BS1/PM2
  events when thresholds apply.
- The upstream adapters in `evidence/upstream.py` (de novo, phasing, segregation,
  phenotype, functional assay, disease mechanism, case-control) map reviewer- or
  pipeline-supplied structured inputs to ACMG criteria, record source
  version/checksum/access date, and emit an explicit "absent"/"malformed" no-call
  rather than guessing. `evidence/cache_manifest.py` writes byte-stable provider
  caches with version/checksum/access-date manifests.

All providers return an `EvidenceBundle` with events, provider versions, source
records, warnings, and match details. gnomAD absence is represented as unknown
evidence, not allele frequency zero.

An extended evidence layer (`evidence/criteria_ext.py`, with thresholds in
`engine/configs/coverage_ext_v1.json`) adds offline-tested, structured-input
providers for PVS1, PS3/BS3, PM3, PP1/BS4, PP4, splice, CNV, non-coding,
complex-indel, mitochondrial, repeat-expansion, and richer structural-variant
evidence. These providers map supplied evidence to ACMG/AMP criteria; they do not
independently discover or validate that evidence from raw biology.

The computational extension (`evidence/alphamissense.py`,
`evidence/computational.py`, and `engine/configs/computational_ext_v1.json`) adds
AlphaMissense, conservation, gene-constraint context, and a documented
REVEL+AlphaMissense consensus rule so computational predictors are not stacked as
independent PP3/BP4 criteria.

Provider caches live under:

```text
data/cache/providers/
```

## ClinVar Enrichment

Current ClinGen enrichment summary:

- ClinVar cases: 21,638
- Direct ClinGen Variation ID matches: 10,649
- Canonical SNV key matches: 940
- Reference-backed indel key matches: 0
- Genomic HGVS fallback matches: 381
- Unmatched cases: 9,668
- Normalization failures: 2
- Cases gaining criteria: 11,970
- Total criteria added: 37,873
- Cases with warnings: 9,700
- Label disagreements among matched records: 30
- Multiple ClinGen match cases resolved deterministically: 2

Canonical-key and genomic-HGVS support are active. The current ERepo-derived
fixture includes usable loci for SNV fallback matches, and a local GRCh38 FASTA is
present in this environment for reference-backed normalization. Native
reference-backed indel fallback yields 0 additional matches on the current real
fixture; genomic-HGVS fallback contributes the current indel lift.

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
- Queue variants for reanalysis by source-snapshot, provider-version, evidence,
  config-version, or conflict-policy trigger, recording an auditable run manifest
  with the trigger cause and run id.
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
  limitations.md
  00-overview.md
  00-orchestrator-agent.md
  pyproject.toml
  requirements.txt
  validation_report.md
  cli.py
  scoring.py
  engine/
    config.py
    config_registry.py
    configs/
      base_v1.json
      coverage_ext_v1.json
      computational_ext_v1.json
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
    alphamissense.py
    computational.py
    criteria_ext.py
    upstream.py
    cache_manifest.py
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
    auth.py
    authz.py
    oidc.py
    audit.py
    observability.py
    deps.py
    settings.py
    service.py
    evidence_resolver.py
    schemas.py
    store.py
    openapi.json
    openapi_contract.py
    cookbook_examples.py
    requirements.txt
    routers/
      alerts.py
      audit.py
      classifications.py
      classify.py
      evidence.py
      reanalysis.py
      reports.py
      validation.py
  frontend/
    index.html
    app.js
    styles.css
    tests/
      test.html
  deploy/
    Dockerfile
    docker-compose.yml
    backup.sh
    restore.sh
    migrations/
  reporting/
    reviewer.py
    summary.py
    render.py
    fhir.py
    common.py
  validation/
    build_fixtures.py
    analyze_failures.py
    analytical_validation.py
    compare_reports.py
    calibration.py
    conflict_policy.py
    fixture_splits.py
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
    cohort_to_evidence.py
    hgvs.py
    identity_audit_report.py
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
    clinical_review.md
    release_policy.md
    conflict_handling.md
    operations_sop.md
    deployment.md
    auth.md
    release_review.md
    api_cookbook.md
  tests/
  data/
    raw/
    cache/providers/
    reference/
      GRCh38.source.json
      GRCh38.fa.meta.json
      install_grch38.sh
      README.md
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
| Architecture | Local proof-of-concept topology with a containerized deploy path (`deploy/`) and a GitHub Actions CI pipeline (`.github/workflows/ci.yml`); a hardened production service topology remains out of scope |
| Data model/schema | SQL schema, apply tooling, storage layer, RLS, evidence-bundle, cohort-count, and reanalysis tests implemented |
| Ingestion/normalization | Canonical identity, reference-free normalization, reference-backed left-alignment, reference-cache status helper, and real-data ingest scripts implemented |
| Scoring | Implemented and tested with file-backed versioned config and VCEP/gene/disease override support |
| Evidence integration | ClinGen, REVEL, gnomAD, AlphaMissense, conservation, gene-constraint, and extended structured-evidence providers implemented with direct-ID/canonical-key/genomic-HGVS match accounting where applicable |
| Monitoring | Tier-crossing diff, reanalysis helpers, operational queue/scheduler/run reports, and alert lifecycle implemented and tested |
| Cohort PS4 | Cohort-count PS4 helpers calibrated to Hearing Loss proband-count specifications for supported dominant genes, plus denominator-aware Cardiomyopathy OR/CI support |
| Reporting/sign-off | Technical reviewer reports, patient-safe summaries, deterministic FHIR Genomics export, draft/released status, and credentialed sign-off workflow implemented |
| API | Tenant-aware FastAPI layer implemented |
| Frontend | Reviewer frontend implemented (mounted at `/reviewer/`) as a proof of concept |
| Security/privacy | SQL RLS plus tenant-isolation, research-boundary, reconstruction, data-governance, and repo-guard tests implemented, plus a proof-of-concept API auth/authz/audit/observability layer (`api/auth.py`, `api/authz.py`, `api/oidc.py`, `api/audit.py`, `api/observability.py`) |
| Validation | Harness, failure analysis, comparison reports, calibration reports, plots, evidence-aware summaries, and generated reports exist |
| Roadmap | `../gap.md` lists unfinished todos; `../roadmap.md` describes the clinical/regulatory pathway |

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

Read `../gap.md` for the unfinished todo list, and `../roadmap.md` for the forward
clinical/regulatory pathway.

The reviewer frontend, API auth/RBAC/audit/observability, RS256/JWKS token
validation, deterministic FHIR serializer, and containerized deployment now exist
as proof-of-concept surfaces. The highest-value remaining work is clinical/product
hardening rather than missing core repo modules: credentialed clinical sign-off, a
formal clinical validation study, data licensing for clinical use, production
identity-provider rollout and deployment hardening, live LIS/EHR integration, and
real-world evidence population/calibration for the structured providers.
