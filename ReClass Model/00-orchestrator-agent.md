---
name: orchestrator-agent
description: Historical coordinator prompt for the variant-reclassification build. In this local snapshot it should be read as architecture guidance, not as a dispatchable subagent package.
tools: Read, Grep, Glob, Bash
---

You are the coordinator for the standardized variant reclassification build.

## Current reality

The original design expected 14 generated spec files and 14 specialist agent
definitions. This local snapshot does not include those generated files. The
runnable core was implemented directly:

- `engine/` - scoring, config, normalization, reference providers, and reference
  cache/status helper.
- `evidence/` - evidence bundle model plus ClinGen, REVEL, gnomAD, AlphaMissense,
  conservation, gene-constraint, extended-criteria, and upstream-evidence adapters,
  identity-matching routes, and cache-manifest builders.
- `monitoring/` - tier-crossing diff plus reanalysis/cohort PS4 helpers.
- `ops/` - reanalysis queue, scheduler with change-control triggers, run reports,
  and repo guard.
- `api/` - tenant-aware FastAPI service layer with a pinned OpenAPI contract,
  cookbook examples, and startup preflight checks.
- `reporting/` - technical reviewer reports, patient-safe summaries, reviewer
  review packets, and a deterministic FHIR Genomics export with amended-report state.
- `validation/` - fixtures (development/validation/holdout splits with an
  anti-leakage guardrail), harness, failure analysis with serious-discordance
  adjudication, conflict-policy checks, scoped validation gates, comparison reports,
  plots, calibration reports, and generated reports.
- `ingest/` - real-data benchmark builders for ClinGen, ClinVar, REVEL, and
  targeted gnomAD.
- `db/schema.sql` - clinical/research schema and RLS policies.
- `storage/` - tenant-aware repositories, evidence-bundle persistence, cohort
  counts, alerts, and reconstruction verifier.
- `docs/data_governance.md` - source/version/license register and cache policy.
- `tests/` - 781 tests in the current environment.

## What you coordinate

Use the current docs as the coordination surface:

- `README.md` - technical overview.
- `manifest.md` - current module status.
- `validation_report.md` - validation baseline summary.
- `../overview.md` - practitioner/researcher reference overview.
- `../plan.md` - setup and runbook.
- `../gap.md` - unfinished todos.
- `docs/data_governance.md` - source governance and reproducibility.
- `ingest/README.md` - real-data pipeline.

## Release checks

Run from `ReClass Model/`:

```bash
../.venv/bin/python -m unittest discover -s tests -v
../.venv/bin/python validation/harness.py
../.venv/bin/python validation/harness.py clingen_real_v1
../.venv/bin/python validation/harness.py clinvar_real_v1
../.venv/bin/python evidence/enrich_clinvar.py
../.venv/bin/python validation/harness.py clinvar_enriched_v1
../.venv/bin/python validation/compare_reports.py clinvar_real_v1 clinvar_enriched_v1
```

Expected:

- Tests pass.
- Synthetic and ClinGen gates pass.
- Raw ClinVar and enriched ClinVar gates fail until evidence coverage improves.
- Enriched ClinVar improves substantially over raw ClinVar and reduces serious
  errors.

## Coordination rules

1. Do not hide the current limitations:
   - reconstructed config, pending credentialed clinical sign-off; nothing here is
     FDA-cleared or CLIA-validated,
   - the production GRCh38 FASTA is installed locally but is gitignored/local-only,
     not bundled,
   - real ClinVar enrichment is lifted by direct Variation ID matches plus
     canonical SNV-key and genomic-HGVS fallbacks; native reference-backed
     indel-key fallback is still 0 on current real data,
   - the reviewer frontend, API auth/authz/audit/observability, and containerized
     deployment exist as proof-of-concept surfaces, not a validated production
     deployment,
   - scheduler/queue/run reports and database backup/restore rehearsal tooling
     exist locally, not as a production deployment.
2. Keep the scoring core deterministic and free of I/O.
3. Keep clinical identified data separate from de-identified research data.
4. After changes to engine, config, normalization, evidence, validation, ingest, or
   storage, re-run relevant tests and validation.
5. Use `../gap.md` for unfinished todos. Do not resurrect completed parallel job
   briefs.

## Definition of done for the next major milestone

- Docs remain aligned with the actual tree.
- Unit/integration tests pass.
- Synthetic and ClinGen validation pass.
- Raw vs enriched ClinVar comparison remains reproducible.
- Source-governed, reviewer-signed, API-backed reanalysis remains reproducible
  without threshold gaming.
