# ReClass Unfinished Todo List

Assessment date: 2026-06-19

This file lists only unfinished work. Completed implementation jobs, historical
coordination notes, and validation baselines belong in the README, roadmap,
overview, and generated reports, not here.

## Latest local review and model test

Review run: 2026-06-19.

Local verification completed:

- `../.venv/bin/python -m unittest discover -s tests -v`: 877 tests passed; 31
  PostgreSQL-backed storage/RLS tests skipped because no local PostgreSQL server
  was running.
- `../.venv/bin/python -m ruff check .`: passed.
- `../.venv/bin/python -m mypy`: passed for the scoped packages (44 source files).
- `frontend/tests/test.html`: 52/52 browser checks passed under headless Chrome.
- `../.venv/bin/python -m engine.reference_cache --status`: local GRCh38 cache is
  loadable and metadata matches the recorded Ensembl release-110 checksum.
- Single-case CLI smoke test (`PVS1` very strong + `PM2` supporting): returned
  `Likely Pathogenic`, 9.0 points, with a stable reconstruction hash.

Model validation still shows the strategic product gap:

| Benchmark | Gate | Definitive concordance | Serious discordance | Interpretation |
|---|---|---:|---:|---|
| `synthetic_v1` | PASS | 92.9% | 0 | Harness/scoring plumbing works. |
| `clingen_real_v1` | PASS | 94.7% | 4 | Complete expert-applied criteria reproduce expert tiers well. |
| `clinvar_real_v1` | FAIL | 5.0% | 34 | Sparse public signals are not enough. |
| `clinvar_enriched_v1` | FAIL | 42.4% | 6 | ClinGen enrichment helps, but evidence coverage is still the blocker. |

Failure analysis for `clinvar_enriched_v1` ranks the next evidence sources by
expected impact. These are **data-integration and clinical-validation** work items,
not scoring-engine fixes — the engine already scores these criteria correctly when
they are supplied:

- ClinGen / functional ACMG criteria (`PVS1`/`PS3`/`PM3`-class): could help 10,905
  mismatched cases, including 3 serious cases.
- gnomAD allele-frequency evidence (`PM2`/`BA1`/`BS1`): could help 9,176
  mismatched cases, including 4 serious cases.
- REVEL computational evidence (`PP3`/`BP4`): could help 8,540 mismatched cases,
  including 2 serious cases.

## Code-actionable backlog — complete

The engine and backend service-layer backlogs are now implemented and covered by the
877-test suite, which is green in this environment. The separate customer-facing
product and commercial code needed to ship a B2B clinical SaaS is **not** included
here — it is a new, largely unbuilt code tranche tracked in its own section below.

- **Core proof-of-concept engine** (deterministic scoring, evidence providers,
  identity matching, normalization, storage/RLS, validation/calibration tooling,
  API, reviewer frontend, reanalysis, FHIR export, CI).
- **Scalable-product feature layer** — the five-area, three-job backlog from the
  2026-06-19 review is finished and merged:
  - Evidence workbench, evidence-coverage dashboards, curation queues, and
    validated VCF/CSV/batch evidence import.
  - Release-gate enforcement (structured sign-off packets, the five-state release
    machine, exportable validation packets) and continuous-reanalysis operations
    (operator views, alert triage, amended-report/notification tracking, tenant
    reanalysis policies).
  - Enterprise platform and security (fail-closed production preflight, OIDC-only
    production auth mode, rate/request limits, audit retention, SLO metrics,
    webhook delivery subsystem, tenant administration and onboarding, generated
    OpenAPI client).

These deliverables are now described in [overview.md](overview.md),
[roadmap.md](roadmap.md), and
[ReClass Model/README.md](ReClass%20Model/README.md), not here. Building them did
**not** change the scoring math in `engine/` and did **not** remove the need for
clinical validation, data licensing, or credentialed sign-off — those remain the
binding gates below.

## B2B clinical SaaS product layer (new product/commercial code — not yet built)

The completed backlog above delivered the deterministic engine, the multi-tenant
service layer, and a thin reviewer/evidence **console** (`frontend/`) that mirrors the
API one endpoint at a time. It did **not** deliver a sellable product. Turning this
into a B2B clinical SaaS — sold to diagnostic/clinical labs and used daily by variant
scientists, reviewers, and lab directors — requires a new and largely unbuilt code
tranche. This is now the **largest remaining software effort**. It is additive to, not
a replacement for, the binding clinical/regulatory gates below, which bind regardless
of how polished the product is.

### Product application and UX (replace the API console)

The current `frontend/` is static HTML/JS that exposes raw JSON and requires a
hand-pasted tenant UUID and bearer token. A production app needs:

- A framework-based web application (component system, state management, design
  system, accessibility / Section 508) replacing the static `index.html` /
  `workbench.html` console.
- SSO/OIDC login UX with an organization/workspace concept and role-aware navigation
  (variant scientist, reviewer, lab director, tenant admin); remove all hand-entered
  tenant IDs and bearer tokens.
- The variant **worklist/queue** is now built as the primary daily surface (the
  `Worklist` tab + the `worklist/` package + the `/worklist` API): case assignment,
  the draft -> in-review -> signed -> released status pipeline, search, status/
  priority/unassigned filters, and turnaround/SLA indicators, replacing the former
  flat draft list. **Bulk actions** are now built too: multi-select in the queue
  (per-row + select-all checkboxes) drives `POST /worklist/cases/bulk/assign` and
  `POST /worklist/cases/bulk/transition`, applying each case independently with
  per-case partial-success reporting (a mixed-status selection transitions the legal
  cases and reports the rest), tenant-scoped, RBAC-gated, and audited as
  `case.bulk_assign` / `case.bulk_transition`. Remaining here: re-homing the queue
  into the framework app below.
- A reviewer **cockpit**: evidence rendered as interpreted clinical cards rather than
  raw JSON, per-criterion rationale, override-with-required-reason, and side-by-side
  previous-vs-new classification diffs for reanalysis.
- Operational dashboards and an in-app notification surface for tier-crossing alerts,
  reanalysis-due, and coverage gaps, on top of the existing alert/reanalysis APIs.

### Case, patient, and PHI context

- A case/order/specimen/ordering-provider data model is now built (`worklist.case`)
  above the de-identified variant key, with an explicit PHI boundary: list/detail
  views are de-identified by default and patient fields are returned only under the
  `case:read_phi` permission, with that access audited. This is a software boundary
  only — introducing patient context still changes the compliance surface and must
  be signed off against the HIPAA review below before any real PHI is stored.
- Consent capture and patient-data-handling UX where the deployment model requires it.

### Clinical reporting as a product

- Render signed classifications as real clinical report documents (templated,
  print/PDF, branded per lab) rather than the current Markdown/JSON output.
- E-signature capture, report amendment/re-issue workflow, and version history
  surfaced in the UI, on top of the existing release-gate state machine and FHIR
  amended-report transitions.

### Customer integrations

- Productized EHR/LIS connectors (e.g., Epic/Cerner and lab LIS) built on the existing
  `reporting/fhir.py` serializer: inbound order intake, outbound report delivery, and
  SMART-on-FHIR launch where applicable — wired only after the per-site integration
  validation already noted below.
- Customer-facing API-key management and an SDK/docs developer portal on top of the
  generated OpenAPI client and `api/webhooks.py`.

### Commercial and account layer

- Self-serve or sales-assisted tenant onboarding UX on top of `ops/onboarding.py` and
  `api/routers/admin.py`.
- Subscription management, usage metering, and billing/invoicing.
- Per-tenant plan/entitlement/feature-flag enforcement.
- Customer support/ticketing, in-app help, a public status page, and customer-visible
  audit/export of a tenant's own data.
- Legal/commercial artifacts surfaced in-product: terms of service, the
  Business-Associate-Agreement workflow, and a data-processing agreement.

### Product-grade reliability and scale

- Load and scale testing at realistic clinical volume, multi-tenant noisy-neighbor
  isolation, and capacity planning beyond the current SLO metrics and local restore
  rehearsal harness.
- Customer-visible SLAs and uptime/turnaround reporting.

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
They cannot be completed by code changes. The software hooks now exist to *enforce*
each of them (structured sign-off packets, scope checks, release-blocking
discordance, conflict-policy exceptions), but the human decisions, agreements, and
study itself remain.

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

The software hooks for these now exist in the repository (RS256/JWKS OIDC in
`api/oidc.py`, fail-closed startup/preflight checks and OIDC-only production auth in
`api/settings.py`, rate/request limits in `api/ratelimit.py`, audit retention and
structured security events in `api/audit.py`, the holdout-split guardrail in
`validation/fixture_splits.py`, change-control reanalysis triggers in
`ops/scheduler.py`, tenant administration/onboarding in `api/routers/admin.py` and
`ops/onboarding.py`, the webhook delivery subsystem in `api/webhooks.py`, SLO
metrics in `api/observability.py`, and the expanded CI in `.github/workflows/ci.yml`),
but the reviews, agreements, and SOP approvals themselves are organizational, not
code.

- Roll out production identity-provider configuration, key-management, and rotation
  policy against the existing RS256/JWKS OIDC support and OIDC-only auth mode.
- Complete security hardening review: TLS/reverse proxy, secrets management, audit
  retention tuning, encryption policy, rate-limit thresholds, least-privilege DB
  roles, and incident-response procedures.
- Complete HIPAA safeguard review, Business Associate Agreements where needed, and
  third-party penetration testing.
- Define production backup retention, monitoring, alerting, high availability, and
  disaster-recovery targets beyond the local restore rehearsal harness and SLO
  dashboards.
- Install, checksum-pin, and validate the production GRCh38 FASTA per deployment
  site; the reference itself remains local-only and is not committed.
- Validate the reviewer and evidence-workbench UIs with human-factors / usability
  testing in the intended user group.
- Establish post-market surveillance operations, clinician notification workflow,
  complaint handling, and periodic re-review cadence on top of the now-built
  reanalysis operations, alert-triage, and amended-report/notification surfaces.
- Convert operations SOPs into approved local procedures with named responsible
  roles and escalation paths.
- Populate the structured evidence providers and the evidence workbench/import
  surfaces (upstream de novo/phasing/segregation/phenotype/functional/disease-
  mechanism/case-control adapters, cohort PS4) from validated upstream sources and
  calibrate them for the Phase 1 clinical scope. The software machinery and offline
  tests exist; the remaining work is real-data integration and clinical validation,
  not new code.
