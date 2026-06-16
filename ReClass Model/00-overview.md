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
  reference-cache status, evidence bundles, ClinGen/REVEL/gnomAD providers,
  canonical identity, versioned config, monitoring diff, reanalysis helpers,
  operational scheduling, API/reporting/sign-off service workflows, validation,
  calibration/comparison reporting, diagnostic plots, real fixtures, ingest
  scripts, database schema, storage adapters, governance docs, and
  PostgreSQL/RLS tests.
- What "validated" means here: validation gates report concordance and serious
  pathogenic/benign discordance for a named fixture and engine version.
- What remains: production deployment, local clinical validation/sign-off,
  optional clinician-facing UI, a supplied GRCh38 FASTA for production indel
  normalization, and broader evidence-provider coverage.

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
7. `../gap.md` - unfinished todos.
8. Source files in `engine/`, `evidence/`, `validation/`, `ingest/`,
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
- Enriched ClinVar validation still fails, but direct ClinGen matches improve
  definitive concordance and reduce serious errors.

## Open items

- Wire a local production GRCh38 FASTA into workflows that need reference-backed
  normalization at scale.
- Refresh source snapshots under `docs/data_governance.md` when updating public
  inputs.
- Broaden evidence providers beyond the current public ClinGen/REVEL/gnomAD slice.
- Clinically review and locally sign off the reconstructed config, VCEP overrides,
  and PS4 rules before real-world use.
- Build a clinician-facing frontend and production deployment/integration layer if
  the project moves beyond the local service proof of concept.
