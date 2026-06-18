# 00 - System Overview and Reading Order

> Module: overview
> Historical owner: `orchestrator-agent`
> Current status: documentation aligned to the local snapshot

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
  API/reporting/sign-off service workflows, the reviewer frontend (mounted at
  `/reviewer/`), proof-of-concept API auth/authz/audit/observability, a
  containerized deployment surface, an operator CLI (`cli.py`), validation,
  calibration/comparison reporting, the single-command analytical-validation report,
  per-case serious-discordance drill-downs, diagnostic plots, real fixtures, ingest
  scripts, database schema, storage adapters, governance docs, and PostgreSQL/RLS
  tests.
- What was most recently completed: upstream-evidence adapters (de novo, phasing,
  segregation, phenotype, functional, disease-mechanism, case-control); byte-stable
  cache manifests; the full identity-route set (Variation ID, Allele ID, canonical
  key, SPDI, MANE/HGVS transcript, genomic HGVS) with ambiguity accounting; MANE
  transcript identity and PS4 denominator/cohort counts in the evidence model;
  development/validation/holdout fixture splits with an anti-leakage guardrail;
  reviewer review packets; serious-discordance adjudication; configurable
  conflict-policy checks; scoped validation gates; locked regression baselines; a
  pinned/drift-checked OpenAPI contract with runnable cookbook examples; FHIR
  amended-report state transitions with replayable payloads; change-control
  reanalysis triggers with an auditable run manifest; startup preflight checks; and
  an expanded GitHub Actions CI pipeline.
- What "validated" means here: validation gates report concordance and serious
  pathogenic/benign discordance for a named fixture and engine version.
- What remains: credentialed clinical sign-off and a formal clinical
  validation study, data licensing for clinical use, production identity-provider
  rollout plus deployment hardening, live LIS/EHR integration, and real-world
  evidence population/calibration for the structured providers.

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
   `monitoring/`, `ops/`, `api/`, `reporting/`, `storage/`, and `db/`.

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
