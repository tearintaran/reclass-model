# Unfinished TODOs

This file lists only unfinished work for the ReClass proof of concept.

## Clinical Review And Validation

- [ ] Have qualified clinical reviewers sign off the reconstructed scoring
  configuration in `engine/configs/base_v1.json`.
- [ ] Confirm each VCEP/gene/disease override against the current published VCEP
  specification before any real-world use.
- [ ] Confirm the cohort PS4 proband-count rules in `monitoring/reanalysis.py`
  against current ClinGen VCEP specifications and local lab policy.
- [ ] Define a local policy for when a draft classification can become clinically
  releasable after human review.
- [ ] Decide how to handle conflicts such as BA1/BS1 frequency evidence versus
  curated pathogenic evidence for known founder variants.

## Reference Data And Variant Coverage

- [ ] Install or document a local production GRCh38 FASTA and checksum for
  reference-backed indel normalization.
- [ ] Re-run identity audits with the GRCh38 FASTA available and record SNV/indel
  duplicate and mismatch rates after reference-backed normalization.
- [ ] Add or ingest source records with usable loci for ClinGen/ERepo records so
  canonical-key fallback matching can produce measurable real-data lift.
- [ ] Extend evidence handling beyond the current ClinGen/REVEL/gnomAD slice,
  especially PVS1, PS3/BS3, PM3, PP1, PP4, splice, CNV, structural-variant,
  repeat-expansion, mitochondrial, non-coding, and complex-indel evidence.
- [ ] Separate true ancestry/population-stratification fields from VCEP/panel group
  fields in real-data fixtures and reports.

## Product And Workflow

- [ ] Build a clinician-facing reviewer application if this project moves beyond
  the current API/report service workflow.
- [ ] Add production authentication, authorization, audit-log retention,
  monitoring, backups, and deployment procedures.
- [ ] Define operational SOPs for reanalysis runs, alert review, sign-off, and
  patient-safe summary release.
- [ ] Decide which validation, calibration, and reanalysis reports should be part
  of routine release review.

## Data Governance And Refresh

- [ ] Re-review ClinVar, ClinGen, REVEL, and gnomAD source terms before any
  non-research or production use.
- [ ] Refresh public source snapshots under `ReClass Model/docs/data_governance.md`
  when intentionally updating fixtures.
- [ ] Record exact source versions, checksums, access dates, and regeneration
  commands for every refreshed fixture.
- [ ] Initialize git and wire `ops/repo_guard.py` as a pre-commit hook if this
  workspace becomes a repository.
