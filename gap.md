# ReClass Unfinished Todo List

Assessment date: 2026-06-17

This file lists only unfinished work. Completed implementation jobs, historical
coordination notes, and validation baselines belong in the README, roadmap,
overview, and generated reports, not here.

## Code-actionable backlog — complete

The parallel code backlog that was tracked here (evidence integration & identity,
validation/calibration/review tooling, and product/API/operations/deployment) is
**finished**: every task is implemented and covered by the 781-test suite, which is
green in this environment. Those deliverables are now described in
[overview.md](overview.md), [roadmap.md](roadmap.md), and
[ReClass Model/README.md](ReClass%20Model/README.md), not here. The only remaining
*software* item is the optional cleanup below; everything else is clinical,
regulatory, legal, data-source, or operational process work that code cannot close.

## Serial-only code work — optional cleanup

This touches many files at once, so run it on a quiet tree when no other change is
in flight.

- Move the top-level packages (`engine/`, `evidence/`, `ingest/`, `validation/`,
  `api/`, `storage/`, `monitoring/`, `ops/`, `reporting/`, `db/`) under a single
  `reclass` namespace (a sweeping import rename across every package). Today the
  `reclass` console entry point exists, but imports are still top-level
  (`from engine.scoring import ...`).

## Binding clinical and regulatory gates (not software — not agent work)

These require credentialed reviewers, counsel, lab leadership, and a formal study.
They cannot be completed by code changes.

- Write and approve the intended-use / indications-for-use statement.
- Determine and document the regulatory pathway with qualified counsel and lab
  leadership.
- Obtain credentialed clinical sign-off for the scoring configuration, VCEP/gene/
  disease overrides, PS4 rules, release policy, and conflict policy.
- Complete current-spec review for every VCEP/gene/disease override that will be
  used in a validated clinical scope.
- Define the validated clinical scope: genes, diseases, inheritance modes, variant
  classes, evidence sources, and out-of-scope exclusions.
- Run a formal clinical validation study on an independent representative cohort
  with pre-registered acceptance criteria and near-zero tolerance for serious
  pathogenic/benign discordance.
- Confirm data licensing for clinical or non-research use of ClinVar, ClinGen,
  REVEL, gnomAD, and any additional source used in production.
- Validate performance across the final scoped populations and variant classes;
  current public fixtures do not support equity or ancestry-performance claims.

## Compliance, security review, and operations (process — not parallel code)

The software hooks for these already exist in the repository (RS256/JWKS OIDC in
`api/oidc.py`, startup/preflight checks in `api/settings.py`, the holdout-split
guardrail in `validation/fixture_splits.py`, the change-control reanalysis triggers
in `ops/scheduler.py`, and the expanded CI in `.github/workflows/ci.yml`), but the
reviews, agreements, and SOP approvals themselves are organizational, not code.

- Roll out production identity-provider configuration, key-management, and rotation
  policy against the existing RS256/JWKS OIDC support.
- Complete security hardening review: TLS/reverse proxy, secrets management, audit
  retention, encryption policy, rate limiting, least-privilege DB roles, and
  incident-response procedures.
- Complete HIPAA safeguard review, Business Associate Agreements where needed, and
  third-party penetration testing.
- Define production backup retention, monitoring, alerting, high availability, and
  disaster-recovery targets beyond the local restore rehearsal harness.
- Install, checksum-pin, and validate the production GRCh38 FASTA per deployment
  site; the reference itself remains local-only and is not committed.
- Validate the reviewer UI with human-factors / usability testing in the intended
  user group.
- Establish post-market surveillance operations, clinician notification workflow,
  complaint handling, and periodic re-review cadence.
- Convert operations SOPs into approved local procedures with named responsible
  roles and escalation paths.
- Populate the structured evidence providers (upstream de novo/phasing/segregation/
  phenotype/functional/disease-mechanism/case-control adapters, cohort PS4) from
  validated upstream sources and calibrate them for the Phase 1 clinical scope. The
  software machinery and offline tests exist; the remaining work is real-data
  integration and clinical validation, not new code.
