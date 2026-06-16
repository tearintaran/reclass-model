# Module Manifest

This manifest maps the current project modules to their status and remaining work.

Historical note: the original design described a generated 14-agent/14-spec
workflow. This local snapshot does not currently include `generate_project.py`, the
full `docs/specs/` tree, the specialist agent definition files, or an
`orchestration/` directory. Treat agent names below as ownership labels from the
original design, not as available files.

## Current module table

| ID | Module | Historical owner | Current files | Status |
|---|---|---|---|---|
| 00 | System overview and reading order | `orchestrator-agent` | `README.md`, `00-overview.md`, `manifest.md`, `../overview.md` | Implemented docs, aligned to current repo |
| 01 | System architecture | `architecture-agent` | current packages plus docs | Local proof-of-concept architecture implemented; production deployment topology remains out of scope |
| 02 | Data model and schema | `data-model-agent` | `db/schema.sql`, `db/apply.py`, `storage/` | Schema, apply tooling, storage adapter, evidence-bundle persistence, cohort counts, reanalysis queue/run tables, and RLS/reconstruction tests implemented |
| 03 | Variant ingestion and normalization | `ingestion-agent` | `engine/normalize.py`, `engine/reference.py`, `engine/reference_cache.py`, `ingest/`, `data/reference/README.md` | Canonical identity, reference-free normalization, reference-backed left-alignment, reference-cache status helper, and provider-backed ingest scripts implemented; production FASTA is local-only and not bundled |
| 04 | Deterministic ACMG/AMP scoring engine | `scoring-agent` | `engine/scoring.py`, `engine/config.py`, `engine/config_registry.py`, `engine/configs/base_v1.json` | Implemented and tested with versioned file-backed config, reconstruction-safe config hashes, and VCEP/gene/disease override support |
| 05 | Evidence integration | `evidence-agent` | `evidence/`, `ingest/*.py`, signal mapping in `engine/scoring.py` | ClinGen, REVEL, and gnomAD providers implemented with provenance, warnings, direct-ID matching, canonical-key fallback support, and match-route accounting |
| 06 | Continuous reanalysis and alerting | `monitoring-agent` | `monitoring/diff.py`, `monitoring/reanalysis.py`, `storage/alerts.py`, `ops/` | Tier-crossing diff, reanalysis primitives, audit events, alert states, scheduler, queue, and run reports implemented |
| 07 | Cohort statistics and PS4 | `cohort-agent` | `db/schema.sql`, `storage/evidence.py`, `monitoring/reanalysis.py` | Cohort-count storage and PS4 derivation implemented with published ClinGen VCEP proband-count rules for supported gene sets plus a conservative fallback |
| 08 | Reporting and sign-off | `reporting-agent` | `reporting/`, `api/routers/reports.py`, `storage/classifications.py` | Technical reviewer reports, patient-safe summaries, and credentialed sign-off workflow implemented |
| 09 | API layer | `api-agent` | `api/` | Tenant-aware FastAPI layer implemented |
| 10 | Reviewer frontend | `frontend-agent` | none | Remaining product work |
| 11 | Security, privacy, and tenancy | `security-agent` | `db/schema.sql` RLS policies, `storage/db.py`, `tests/test_storage.py`, `.gitignore`, `ops/repo_guard.py`, `docs/data_governance.md` | RLS, tenant isolation, research-boundary tests, source-governance docs, and commit hygiene guard implemented |
| 12 | Validation gate, failure analysis, and concordance harness | `validation-agent` | `validation/`, `tests/`, `../plots/` | Harness, failure analysis, comparison reports, calibration reports, diagnostic plots, and tests implemented |
| 13 | Roadmap | `roadmap-agent` | `../gap.md` | Unfinished todo list |

## Validation gates

Run from `ReClass Model/`:

```bash
../.venv/bin/python -m unittest discover -s tests -v
../.venv/bin/python validation/harness.py
../.venv/bin/python validation/harness.py clingen_real_v1
../.venv/bin/python validation/harness.py clinvar_real_v1
../.venv/bin/python validation/harness.py clinvar_enriched_v1
../.venv/bin/python validation/compare_reports.py clinvar_real_v1 clinvar_enriched_v1
../.venv/bin/python validation/calibration.py clingen_real_v1
../.venv/bin/python -m engine.reference_cache --status
```

Expected current outcomes:

| Gate | Expected result | Meaning |
|---|---|---|
| Unit/integration tests | 389 tests passing in the current environment | Engine, evidence providers, normalization, reference cache, API/reporting, monitoring, ops/reanalysis, validation/calibration, comparison reports, gate logic, storage/RLS/reconstruction, governance, and bundle-provenance tests |
| Synthetic validation | PASS | Harness and scoring plumbing are working |
| ClinGen real validation | PASS | Complete expert-applied criteria reproduce VCEP calls well |
| Raw ClinVar validation | FAIL | Sparse public evidence exposes missing evidence integration |
| Enriched ClinVar validation | FAIL, improved | Direct ClinGen matches improve ClinVar concordance but do not cover enough evidence |
| Raw vs enriched comparison | PASS | Quantifies before/after improvement from ClinGen criteria |

## Honest status of the build

Implemented and tested:

- Scoring engine and reconstruction hash.
- Reference-free and reference-backed normalization plus canonical provider/storage
  identity helpers.
- GRCh38 reference-cache status helper.
- Tier-crossing alert diff.
- Evidence bundle model and ClinGen, REVEL, and gnomAD providers with match-route
  accounting.
- Synthetic, ClinGen real, ClinVar raw, and ClinVar enriched fixtures.
- Validation harness, evidence-aware summaries, failure-analysis tooling,
  comparison reports, calibration tooling, and diagnostic plots.
- Real-data ingest scripts for ClinGen, ClinVar, REVEL, and targeted gnomAD.
- Storage layer plus schema apply tooling with PostgreSQL RLS, research-boundary,
  evidence-bundle provenance, cohort-count, reanalysis, and reconstruction
  integration tests.
- Tenant-aware API and report/sign-off service workflow.
- Operational reanalysis queue, scheduler, run reports, and retry/error handling.
- Data-governance docs, commit policy, and repo guard.

Implemented but still narrow:

- Current ClinGen ERepo fixtures have no usable locus fields for the
  canonical-key index, so real ClinVar enrichment still gains evidence through
  direct Variation ID matches.
- Reference-backed normalization exists, but a production GRCh38 FASTA is not
  bundled and must be supplied locally for reference-anchored indel workflows.
- VCEP/gene/disease overrides and cohort PS4 rules are reviewable and tested, but
  they still require current-spec review and local clinical sign-off.

Remaining work:

- Clinician-facing frontend, if wanted.
- Production deployment, authentication/authorization integration, and operating
  procedures.
- Broader evidence-provider coverage beyond the current ClinGen/REVEL/gnomAD
  slice, especially splice, structural, CNV, mitochondrial, non-coding, and complex
  indel evidence.
- Local clinical validation and sign-off of config/PS4 rules before any real-world
  use.

## Next roadmap

Use `../gap.md` for unfinished todos only.
