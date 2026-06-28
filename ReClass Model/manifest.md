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
| 02 | Data model and schema | `data-model-agent` | `db/schema.sql`, `db/apply.py`, `storage/`, `worklist/` | Schema, apply tooling, storage adapter, evidence-bundle persistence, cohort counts, reanalysis queue/run tables, tenant-scoped worklist cases, and RLS/reconstruction tests implemented |
| 03 | Variant ingestion and normalization | `ingestion-agent` | `engine/normalize.py`, `engine/reference.py`, `engine/reference_cache.py`, `ingest/`, `data/reference/README.md` | Canonical identity, reference-free normalization, reference-backed left-alignment, reference-cache status helper, and provider-backed ingest scripts implemented; production FASTA is local-only and not bundled |
| 04 | Deterministic ACMG/AMP scoring engine | `scoring-agent` | `engine/scoring.py`, `engine/config.py`, `engine/config_registry.py`, `engine/configs/base_v1.json` | Implemented and tested with versioned file-backed config, reconstruction-safe config hashes, and VCEP/gene/disease override support |
| 05 | Evidence integration | `evidence-agent` | `evidence/`, `ingest/*.py`, signal mapping in `engine/scoring.py`, `engine/configs/coverage_ext_v1.json`, `engine/configs/computational_ext_v1.json` | ClinGen, REVEL, gnomAD, AlphaMissense, conservation, gene-constraint, extended structured-evidence, and upstream-evidence adapters (de novo/phasing/segregation/phenotype/functional/disease-mechanism/case-control) implemented with provenance, warnings, byte-stable cache manifests, and Variation-ID/Allele-ID/canonical-key/SPDI/MANE-HGVS/genomic-HGVS matching with ambiguity accounting; MANE transcript identity and PS4 denominator/cohort counts carried in the evidence model |
| 06 | Continuous reanalysis and alerting | `monitoring-agent` | `monitoring/diff.py`, `monitoring/reanalysis.py`, `storage/alerts.py`, `ops/` | Tier-crossing diff, reanalysis primitives, audit events, alert states, scheduler with source-snapshot/provider-version/config/conflict-policy change-control triggers and auditable run manifests, queue, and run reports implemented |
| 07 | Cohort statistics and PS4 | `cohort-agent` | `db/schema.sql`, `storage/evidence.py`, `monitoring/reanalysis.py` | Cohort-count storage and PS4 derivation implemented with Hearing Loss proband-count rules for supported dominant genes, Cardiomyopathy OR/CI support when denominators are supplied, and a conservative fallback |
| 08 | Reporting and sign-off | `reporting-agent` | `reporting/`, `api/routers/reports.py`, `storage/classifications.py` | Technical reviewer reports, patient-safe summaries, reviewer review packets, a deterministic FHIR Genomics serializer with draft/final/amended report state and replayable outbound payloads, and credentialed sign-off workflow implemented |
| 09 | API layer | `api-agent` | `api/` | Tenant-aware FastAPI layer implemented, with classification/evidence/report/reanalysis/admin/webhook/worklist routes, a pinned/drift-checked OpenAPI artifact, runnable cookbook examples, and startup preflight checks for env vars, OIDC/JWKS, audit backend, DB role, reference-FASTA metadata, and provider-cache manifests |
| 10 | Reviewer frontend | `frontend-agent` | `frontend/` (`index.html`, `app.js`, `styles.css`, `workbench.*`, `tests/test.html`) | Implemented as a proof of concept: reviewer web app mounted at `/reviewer/`, with the case worklist as its default daily surface plus resolve/classify/draft/report/sign-off/alert and evidence-workbench flows; hardened with API-driven provider discovery, in-memory token by default, structured views, loading/error/empty states, small-viewport layout, and an 80-check dependency-free browser harness |
| 11 | Security, privacy, and tenancy | `security-agent` | `db/schema.sql` RLS policies, `storage/db.py`, `tests/test_storage.py`, `.gitignore`, `ops/repo_guard.py`, `docs/data_governance.md`, `api/auth.py`, `api/authz.py`, `api/oidc.py`, `api/audit.py`, `api/observability.py` | RLS, tenant isolation, research-boundary tests, source-governance docs, and commit hygiene guard implemented, plus a proof-of-concept API hardening layer: RS256/JWKS OIDC, HS256 JWT + API-key auth, RBAC, audit logging, and `/health` + `/metrics` observability |
| 12 | Validation gate, failure analysis, and concordance harness | `validation-agent` | `validation/`, `tests/`, `../plots/` | Harness, failure analysis (including per-case serious-discordance drill-down and adjudication with release-blocking status), single-command analytical-validation report with VCEP/gene/disease/population/variant-class scoped gates, development/validation/holdout fixture splits with an anti-leakage guardrail, a pre-registered blinded held-out evaluator with pinned config/partition fingerprints and confidence intervals, configurable conflict-policy checks, reviewer review packets, comparison reports, locked regression baselines, calibration reports, diagnostic plots, and tests implemented |
| 13 | Roadmap | `roadmap-agent` | `../gap.md` | Unfinished todo list |
| 14 | Scalable product feature layer | `product` | `evidence/workbench.py`, `evidence/coverage.py`, `evidence/curation.py`, `ingest/batch_import.py`, `ingest/vcf_import.py`, `ingest/csv_import.py`, `validation/signoff.py`, `validation/release_gate.py`, `validation/release_packet.py`, `ops/onboarding.py`, `worklist/`, `api/ratelimit.py`, `api/webhooks.py`, `api/generated_client.py`, `api/routers/admin.py`, `api/routers/webhooks.py`, `api/routers/worklist.py`, `storage/admin.py`, `storage/webhooks.py`, `storage/worklist.py`, `deploy/migrations/003`–`006`, `frontend/workbench.*`, `docs/evidence_workbench.md` | Built and tested: evidence workbench/coverage/curation and batch/VCF/CSV import; five-state release-gate sign-off, exportable validation packets, reanalysis operator views, alert triage, amended-report/notification tracking; fail-closed preflight, OIDC-only auth, rate/request limits, audit retention, SLO metrics, webhook delivery, tenant administration/onboarding, generated OpenAPI client, and a case worklist with assignment, SLA/state tracking, bulk actions, and a permissioned PHI boundary. Real-evidence population, credentialed sign-off, data licensing, production rollout, and a framework-based product UI remain (see `../gap.md`) |

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
../.venv/bin/python validation/holdout_eval.py
../.venv/bin/python -m engine.reference_cache --status
../.venv/bin/python ops/verify_distribution.py
```

Expected current outcomes:

| Gate | Expected result | Meaning |
|---|---|---|
| Unit/integration tests | 945 run successfully: 914 pass and 31 PostgreSQL-backed tests skip without a local server | Engine, evidence providers, normalization, reference cache, API/reporting/FHIR/contract/cookbook, worklist/PHI/RBAC/bulk actions, monitoring, ops/reanalysis, held-out validation guardrails/evaluation, calibration/analytical-validation/conflict-policy/review packets, CLI, preflight, storage/RLS/reconstruction, governance, and the scalable-product feature layer |
| Synthetic validation | PASS | Harness and scoring plumbing are working |
| ClinGen real validation | PASS | Complete expert-applied criteria reproduce VCEP calls well |
| Raw ClinVar validation | FAIL | Sparse public evidence exposes missing evidence integration |
| Enriched ClinVar validation | FAIL, improved | Direct ClinGen matches improve ClinVar concordance but do not cover enough evidence |
| Raw vs enriched comparison | PASS | Quantifies before/after improvement from ClinGen criteria |
| Pre-registered held-out evaluation | PASS | ClinGen holdout definitive concordance 95.4% (95% CI 94.5–96.1%); serious discordance 0.1% (95% upper bound 0.2%) |
| Installable distribution | PASS | Built wheel contains and can load the worklist, reviewer assets, schema, migrations, API contract, and independent service version |

## Honest status of the build

Implemented and tested:

- Scoring engine and reconstruction hash.
- Reference-free and reference-backed normalization plus canonical provider/storage
  identity helpers.
- GRCh38 reference-cache status helper.
- Tier-crossing alert diff.
- Evidence bundle model and ClinGen, REVEL, gnomAD, AlphaMissense, conservation,
  gene-constraint, and extended structured-evidence providers with match-route
  accounting where applicable.
- Synthetic, ClinGen real, ClinVar raw, and ClinVar enriched fixtures.
- A deterministic 30% cross-fixture held-out partition, frozen pre-registration,
  and CI-gated evaluator with Wilson confidence intervals.
- Validation harness, evidence-aware summaries, failure-analysis tooling
  (including per-case serious-discordance drill-down), a single-command
  analytical-validation report generator, comparison reports, calibration tooling,
  and diagnostic plots.
- Operator CLI (`cli.py`) wrapping classify, validate, reference-cache status,
  before/after compare, calibration, and report regeneration, with `--json` output.
- Real-data ingest scripts for ClinGen, ClinVar, REVEL, and targeted gnomAD.
- Storage layer plus schema apply tooling with PostgreSQL RLS, research-boundary,
  evidence-bundle provenance, cohort-count, reanalysis, and reconstruction
  integration tests.
- Tenant-aware API and report/sign-off service workflow, plus deterministic FHIR
  Genomics export.
- Operational reanalysis queue, scheduler, run reports, and retry/error handling.
- Data-governance docs, commit policy, and repo guard.
- The scalable-product feature layer: evidence workbench/coverage/curation,
  PHI-scrubbing batch import and VCF/CSV variant import with dry-run reports, the
  five-state release-gate sign-off machine with structured packets and exportable
  validation packets, reanalysis operator views and per-tenant policies, alert
  triage and amended-report/notification tracking, fail-closed/OIDC-only platform
  security with rate limiting and audit retention, SLO metrics, the signed webhook
  delivery subsystem, tenant administration/onboarding, and a generated OpenAPI
  client.
- The tenant-scoped case worklist: case/order/specimen context, assignment,
  priority and SLA views, audited state transitions, bulk actions with per-case
  results, classification links, PHI-redacted defaults, separately permissioned
  PHI access, and in-memory/PostgreSQL stores.

Implemented but still narrow:

- The ERepo-derived fixture now carries usable loci (via `ingest/hgvs.py`), so
  beyond direct Variation ID matches the canonical SNV-key fallback contributes
  940 additional matches and genomic-HGVS fallback contributes 381 matches on
  current real data. Native reference-backed indel-key fallback is still 0 on the
  current fixture.
- Reference-backed normalization exists, and a local GRCh38 FASTA is now
  installed locally (Ensembl release-110); the reference cache reports it present
  and identity audits were re-run against it. The FASTA is not bundled: it remains
  gitignored/local-only and is not committed, so reference-anchored indel
  workflows still require it to be supplied locally.
- VCEP/gene/disease overrides and cohort PS4 rules are reviewable and tested,
  including Cardiomyopathy OR/CI support when denominators are supplied, but they
  still require current-spec review and local clinical sign-off.

Remaining work:

- The clinician-facing reviewer frontend is built as a proof of concept.
- Production deployment and authentication/authorization exist as proof-of-concept
  surfaces (containerized deploy, RS256/JWKS OIDC, HS256 JWT/API-key auth, RBAC,
  audit, observability), but still need production identity-provider rollout and
  deployment hardening.
- Broad evidence-provider machinery exists for structured inputs, including
  repeat-expansion, mitochondrial, non-coding, complex-indel, and richer
  structural-variant signals. Remaining work is source integration, calibration,
  and clinical validation of those inputs.
- Credentialed clinical sign-off and local clinical validation of config/PS4 rules
  remain pending before any real-world use.

## Next roadmap

Use `../gap.md` for unfinished todos only.
