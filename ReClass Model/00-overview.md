# 00 - System Overview and Reading Order

> Module: overview
> Historical owner: `orchestrator-agent`
> Current status: documentation aligned to the 2026-06-23 local review

This document is the compact technical overview for contributors. The
practitioner/researcher overview lives at `../overview.md`; unfinished todos live
at `../gap.md`.

## Purpose

Orient contributors to the current local project:

- What the system is: a deterministic ACMG/AMP variant reclassification engine.
- What currently works: scoring, normalization, reference providers,
  reference-cache status (the GRCh38 FASTA is now installed locally), evidence
  bundles, ClinGen/REVEL/gnomAD/AlphaMissense/computational providers plus the
  extended PVS1/PS3/BS3/PM3/PP1/PP4/splice/CNV/non-coding/complex-indel/
  mitochondrial/repeat/SV criteria layer, canonical identity, versioned
  config, monitoring diff, reanalysis helpers, operational scheduling,
  API/reporting/sign-off service workflows, the tenant-scoped case worklist and
  PHI boundary, the reviewer frontend (mounted at `/reviewer/`), proof-of-concept
  API auth/authz/audit/observability, a
  containerized deployment surface, an operator CLI (`cli.py`), validation,
  calibration/comparison reporting, the single-command analytical-validation report,
  per-case serious-discordance drill-downs, diagnostic plots, real fixtures, ingest
  scripts, database schema, storage adapters, governance docs, and PostgreSQL/RLS
  tests.
- What was most recently completed: the 2026-06-23 **pre-registered blinded
  held-out evaluation** — a deterministic 30% holdout keyed on GRCh38 identity,
  hidden from calibration, with a hash-pinned engine/config, frozen acceptance
  criteria, partition fingerprints, Wilson confidence intervals, and a CI gate.
  The primary ClinGen hypothesis passes at 95.4% definitive concordance with a
  94.5% lower confidence bound and a 0.2% upper confidence bound on serious
  discordance.
- The 2026-06-19 **scalable-product feature
  layer** — an evidence workbench (`evidence/workbench.py`, `coverage.py`,
  `curation.py`) with batch/VCF/CSV import (`ingest/{batch,vcf,csv}_import.py`); an
  enforced release-gate sign-off state machine and exportable validation packets
  (`validation/signoff.py`, `release_gate.py`, `release_packet.py`); continuous
  reanalysis operations, alert triage, and amended-report/notification tracking
  (`monitoring/`, `ops/`, `storage/alerts.py`, `reporting/fhir.py`); and an
  enterprise platform/security layer (fail-closed preflight and OIDC-only auth in
  `api/settings.py`, rate/request limits in `api/ratelimit.py`, audit retention and
  security events in `api/audit.py`, SLO metrics in `api/observability.py`, the
  webhook delivery subsystem in `api/webhooks.py`, tenant administration/
  onboarding in `api/routers/admin.py`/`ops/onboarding.py`, and the case worklist
  in `worklist/`, `storage/worklist.py`, and `api/routers/worklist.py`). This built
  on the prior upstream-evidence adapters, byte-stable cache manifests, full
  identity-route set with ambiguity accounting, MANE/PS4 evidence-model fields,
  fixture splits with an anti-leakage guardrail, reviewer review packets,
  conflict-policy checks, scoped validation gates, locked regression baselines,
  the pinned/drift-checked OpenAPI contract, FHIR amended-report transitions,
  change-control reanalysis triggers, startup preflight checks, and the expanded
  GitHub Actions CI pipeline.
- What "validated" means here: validation gates report concordance and serious
  pathogenic/benign discordance for a named fixture and engine version.
- Latest local verification: 945 tests ran successfully (914 passed and 31
  PostgreSQL-backed storage/RLS tests skipped locally without PostgreSQL);
  `ruff`, scoped `mypy`, the frontend
  browser harness (80/80), repo guard, dependency check, validation baselines,
  held-out gate, and the GRCh38 reference-cache status check passed. Docker checks
  were unavailable because Docker is not installed locally.
- What remains: credentialed clinical sign-off and a formal clinical
  validation study, data licensing for clinical use, production identity-provider
  rollout plus deployment hardening, live LIS/EHR integration, and real-world
  evidence population/calibration for the structured providers and the evidence
  workbench. The scalable-product feature layer is now **built** in software; the
  remaining work in those areas is clinical, data, and infrastructure hardening, not
  missing code (see `../gap.md`).

## Current data path

```text
fixture or variant record
  -> normalization / canonical identity
  -> supplied signals or provider evidence bundle
  -> standardized ACMG criteria
  -> deterministic scoring
  -> classification receipt and reconstruction hash
  -> validation/calibration report, API response, or persisted classification
```

The broader service data path is:

```text
raw public and clinical sources
  -> tenant worklist case / accession (clinical workflow)
  -> canonical variant identity
  -> evidence provider layer with provenance
  -> standardized ACMG criteria
  -> deterministic scoring
  -> storage
  -> reanalysis queue/run report and tier-crossing alert
  -> human sign-off
  -> report
```

## Reading order

1. `../overview.md` - practitioner/researcher project explanation.
2. `README.md` - technical overview and current repository layout.
3. `manifest.md` - module status map.
4. `validation_report.md` - current validation summary.
5. `ingest/README.md` - real-data benchmark pipeline.
6. `../plan.md` - setup and run commands.
7. `cli.py` - operator CLI (`reclass`) over the runnable workflows.
8. `../gap.md` - unfinished todos.
9. Source files in `engine/`, `evidence/`, `validation/`, `ingest/`,
   `monitoring/`, `worklist/`, `ops/`, `api/`, `reporting/`, `storage/`, and `db/`.

## Validation commands

Run from `ReClass Model/`:

```bash
../.venv/bin/python -m unittest discover -s tests -v
../.venv/bin/python validation/harness.py
../.venv/bin/python validation/harness.py clingen_real_v1
../.venv/bin/python validation/harness.py clinvar_real_v1
../.venv/bin/python evidence/enrich_clinvar.py
../.venv/bin/python validation/harness.py clinvar_enriched_v1
../.venv/bin/python validation/compare_reports.py clinvar_real_v1 clinvar_enriched_v1
../.venv/bin/python validation/calibration.py clingen_real_v1
../.venv/bin/python validation/holdout_eval.py
../.venv/bin/python -m engine.reference_cache --status
```

Expected current interpretation:

- Unit/integration tests pass.
- Synthetic validation passes.
- ClinGen real validation passes when the engine is fed expert-applied criteria.
- Raw ClinVar validation fails because the fixture lacks complete ACMG evidence.
- Enriched ClinVar validation still fails, but direct ClinGen matches plus
  canonical-key and genomic-HGVS fallbacks improve definitive concordance and
  reduce serious errors.
- The pre-registered primary held-out ClinGen hypothesis passes, while the held-out
  sparse-vs-enriched ClinVar contrast confirms evidence completeness is the blocker.

## Open items

- Obtain credentialed clinical sign-off and run a formal clinical validation
  study; clinically review and locally sign off the reconstructed config, VCEP
  overrides, and PS4 rules before real-world use.
- Secure data licensing for clinical use.
- Refresh source snapshots under `docs/data_governance.md` when updating public
  inputs.
- Broaden evidence providers beyond the current ClinGen/REVEL/gnomAD slice and the
  extended structured-provider layer by wiring validated upstream sources and
  clinical review workflows to those inputs.
- Roll out a real identity provider against the existing RS256/JWKS OIDC support
  and harden the proof-of-concept deployment and API auth/audit surfaces for
  production.
- Convert the implemented migration ledger, backup script, restore script, and
  local restore rehearsal into production disaster-recovery policy.
- Connect the deterministic FHIR Genomics serializer to live LIS/EHR workflows
  only after local integration validation.
- Populate and operationalize the now-built scalable-product feature layer
  (evidence workbench and coverage dashboards, enforced sign-off packets,
  operational reanalysis dashboards and notifications, fail-closed production
  deployment checks, and customer-facing import/API/integration surfaces) with
  validated upstream evidence, credentialed sign-off, data licensing, and a
  production identity-provider/deployment rollout. The software exists and is tested;
  the remaining work is non-code (see `../gap.md`).
