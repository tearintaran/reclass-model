# ReClass Model - Setup and Runbook

This is the practical setup guide for the **Standardized Variant Reclassification
Engine** in `ReClass Model/`.

The project is a local proof of concept for deterministic ACMG/AMP variant
classification. It has a working scoring engine, evidence-provider slice,
versioned configuration, validation/calibration reports, tenant-aware API,
reviewer-reporting/sign-off service workflow, operational reanalysis tooling,
PostgreSQL storage/RLS layer, real-data ingestion scripts, source-governance
policy, and generated benchmark reports. It is not a finished clinical product.

## 1. Current state

| Path | Current role |
|---|---|
| `overview.md` | Practitioner/researcher reference overview |
| `limitations.md` | Clinical and scientific boundary statement |
| `gap.md` | Unfinished todo list |
| `environment_audit.md` | Python, terminal, pip, Homebrew, and validation audit |
| `ReClass Model/README.md` | Technical overview and repository layout |
| `ReClass Model/manifest.md` | Module/status map |
| `ReClass Model/engine/` | Deterministic scoring, versioned config, canonical identity, normalization, reference providers, and reference-cache helper |
| `ReClass Model/evidence/` | Evidence bundle model plus ClinGen, REVEL, gnomAD, AlphaMissense, conservation, gene-constraint, and extended structured-evidence providers |
| `ReClass Model/api/` | Tenant-aware FastAPI service layer |
| `ReClass Model/reporting/` | Technical reviewer and patient-safe summary reports |
| `ReClass Model/validation/` | Fixtures, harness, failure analysis, comparison tool, calibration reports, plots, and generated reports |
| `ReClass Model/ingest/` | ClinGen, ClinVar, REVEL, and targeted gnomAD benchmark builders |
| `ReClass Model/storage/` + `ReClass Model/db/` | PostgreSQL schema, apply tool, tenant/RLS storage layer, evidence-bundle persistence, cohort counts, and reconstruction verifier |
| `ReClass Model/ops/` | Reanalysis queue, scheduler, run reports, repo guard (wired as the `.git/hooks/pre-commit` hook), and tenant onboarding |
| `ReClass Model/api/auth.py`, `authz.py`, `audit.py`, `observability.py`, `ratelimit.py`, `webhooks.py` | JWT/API-key + OIDC auth, RBAC, audit log/retention/security events, `/health` + `/metrics` SLO metrics, rate/request limits, and the webhook delivery subsystem |
| `ReClass Model/evidence/workbench.py`, `coverage.py`, `curation.py` | Reviewer evidence workbench, evidence-coverage roll-ups, and curation queues |
| `ReClass Model/ingest/batch_import.py`, `vcf_import.py`, `csv_import.py` | PHI-scrubbing upstream-evidence batch import and VCF/CSV variant import with dry-run reports |
| `ReClass Model/validation/signoff.py`, `release_gate.py`, `release_packet.py` | Five-state release-gate sign-off, scope/preflight/discordance blocking, and exportable validation packets |
| `ReClass Model/frontend/` | Static clinician reviewer UI at `/reviewer/` plus the evidence-workbench page (`workbench.html`) |
| `ReClass Model/deploy/` | Containerized deployment plus backup/restore scripts and the migration ledger: `Dockerfile`, `docker-compose.yml`, `backup.sh`, `restore.sh`, `migrations/001`–`005` |
| `ReClass Model/docs/` | Governance, clinical review, release policy, conflict handling, operations SOP, deployment, auth, release review, API cookbook, and evidence-workbench docs |
| `ReClass Model/cli.py` | Operator CLI (`reclass`) wrapping classify, validate, reference status, compare, calibration, and report regeneration |
| `ReClass Model/tests/` | 877 tests in the current environment |
| `plots/` | PNG diagnostics generated from validation reports |

Verified status:

- Unit/integration suite: 877 tests passing in the current environment.
- Synthetic validation: PASS, 92.9% definitive concordance.
- ClinGen real validation: PASS, 94.7% definitive concordance.
- Raw ClinVar validation: expected FAIL, 5.0% definitive concordance.
- ClinVar enriched with direct ClinGen Variation ID, canonical SNV-key, and
  genomic-HGVS matches: expected FAIL, but improved to 42.4% definitive
  concordance and 6 serious errors.
- REVEL and gnomAD providers: implemented with mocked/offline unit tests and local
  provider caches under `data/cache/providers/`.
- AlphaMissense, conservation, gene-constraint, and extended structured-evidence
  providers are implemented with offline tests and versioned config files
  (`computational_ext_v1.json`, `coverage_ext_v1.json`).
- API/reporting/sign-off: implemented and tested without requiring a live database.
- Storage/ops/reanalysis: implemented and tested against PostgreSQL in the current
  environment.
- Reference cache status helper: implemented; a local GRCh38 FASTA (Ensembl
  release-110, 3.1 GB) is now installed and the reference cache reports it present.
  Identity audits were re-run with reference-backed left-alignment (see
  `ReClass Model/validation/reports/identity_audit_grch38.md`). The FASTA remains
  gitignored/local-only and is not committed.

## 2. Prerequisites

| Dependency | Required for | Status/notes |
|---|---|---|
| Python 3.14.6 | Core engine, tests, validation, ingest scripts | Project venv is `.venv/` |
| pip 26.1.2 | Package management | Current venv is clean by `pip check` |
| matplotlib | Diagnostic plots | Listed in requirements; harness continues if absent |
| curl | gnomAD targeted API enrichment | Used by `ingest/enrich_gnomad.py` |
| PostgreSQL 16 | Storage layer + RLS/reconstruction integration tests | Schema and storage tests are implemented |
| `psycopg` | Storage layer + DB tests | Installed from `psycopg[binary]` |
| FastAPI / uvicorn / httpx | API service and API tests | Component deps in `ReClass Model/api/requirements.txt` |
| GRCh38 FASTA | Production-scale reference-backed normalization | Local-only cache; not committed |

The scoring engine itself uses only the Python standard library. Storage requires
`psycopg`; plots require `matplotlib`; API serving/tests require the component
dependencies in `api/requirements.txt`.

## 3. Python environment

From the project root:

```bash
cd "/Users/taranramadoss/Documents/Projects/First Project"
source .venv/bin/activate
```

Or run commands directly through the venv:

```bash
cd "/Users/taranramadoss/Documents/Projects/First Project/ReClass Model"
../.venv/bin/python -m unittest discover -s tests -v
```

To install or refresh dependencies from the manifest:

```bash
cd "/Users/taranramadoss/Documents/Projects/First Project"
.venv/bin/python -m pip install -r "ReClass Model/requirements.txt"
.venv/bin/python -m pip install -r "ReClass Model/api/requirements.txt"
.venv/bin/python -m pip check
```

## 4. Run the test suite

```bash
cd "/Users/taranramadoss/Documents/Projects/First Project/ReClass Model"
../.venv/bin/python -m unittest discover -s tests -v
```

Expected current result:

```text
Ran 877 tests
OK
```

PostgreSQL/RLS integration tests skip cleanly if a database is not reachable. In
the current audited environment PostgreSQL and `psycopg` are available, so the full
877-test suite executes.

## 5. Run validation

Run from `ReClass Model/`:

```bash
PY="../.venv/bin/python"

$PY validation/harness.py
$PY validation/harness.py clingen_real_v1
$PY validation/harness.py clinvar_real_v1
$PY validation/harness.py clinvar_enriched_v1
$PY validation/calibration.py clingen_real_v1
```

Expected current outcomes:

| Benchmark | Gate | Cases | Definitive concordance | Serious discordance | Overall exact concordance |
|---|---|---:|---:|---:|---:|
| `synthetic_v1` | PASS | 32 | 92.9% | 0 | 93.8% |
| `clingen_real_v1` | PASS | 12,446 | 94.7% | 4 | 93.0% |
| `clinvar_real_v1` | FAIL | 21,638 | 5.0% | 34 | 19.9% |
| `clinvar_enriched_v1` | FAIL | 21,638 | 42.4% | 6 | 46.6% |

The ClinVar failures are expected. Raw ClinVar mostly contains labels plus partial
frequency/REVEL evidence, not complete structured ACMG evidence. The enriched
fixture proves that adding matched ClinGen criteria helps, but also proves that
evidence coverage is still incomplete.

Reports are written to `validation/reports/`.

## 6. Evidence providers and ClinVar enrichment

The evidence-provider layer is implemented in `ReClass Model/evidence/`.
ClinGen matching enriches ClinVar cases by ClinVar Variation ID plus canonical-key
fallback and appends ClinGen-applied ACMG criteria while preserving the original
ClinVar expected label. REVEL and gnomAD providers expose the same evidence used
by their ingest scripts as reusable, cached, provenance-rich `EvidenceBundle`s.
AlphaMissense, conservation, gene-constraint, and extended structured-evidence
providers are also implemented; they map supplied evidence to criteria/context but
do not discover or validate that evidence on their own.

```bash
cd "/Users/taranramadoss/Documents/Projects/First Project/ReClass Model"
../.venv/bin/python evidence/enrich_clinvar.py
../.venv/bin/python -m unittest tests.test_revel_provider tests.test_gnomad_provider tests.test_computational_providers tests.test_criteria_ext_provider -v
../.venv/bin/python validation/harness.py clinvar_enriched_v1
../.venv/bin/python validation/analyze_failures.py clinvar_enriched_v1
../.venv/bin/python validation/compare_reports.py clinvar_real_v1 clinvar_enriched_v1
```

Current enrichment summary:

- ClinVar cases: 21,638
- Direct ClinGen Variation ID matches: 10,649
- Canonical SNV key matches: 940
- Reference-backed indel key matches: 0
- Genomic HGVS fallback matches: 381
- Normalization failures: 2
- Unmatched: 9,668
- Cases gaining criteria: 11,970
- Total criteria added: 37,873
- Cases with warnings: 9,700
- Label disagreements among matched records: 30
- Multiple-match cases resolved deterministically: 2

## 7. Diagnostic plots

Every harness run writes PNG diagnostics to `plots/` at the project root. The gate
does not depend on plotting.

To regenerate plots from existing reports:

```bash
cd "/Users/taranramadoss/Documents/Projects/First Project/ReClass Model"
../.venv/bin/python validation/plots.py
```

Current plot set includes confusion matrices, tier distributions, concordance plots,
and a summary plot for synthetic, ClinGen, raw ClinVar, and enriched ClinVar.

## 8. Reference cache status

The code can use `engine.reference.FastaReference` for reference-backed
normalization. Whole-genome FASTA files are intentionally not committed. A local
GRCh38 FASTA (Ensembl release-110, 3.1 GB) is now installed in this environment and
the helper reports it present, but the FASTA must still be supplied per-environment
(it is gitignored/local-only and is not committed).

Check the current cache status:

```bash
cd "/Users/taranramadoss/Documents/Projects/First Project/ReClass Model"
../.venv/bin/python -m engine.reference_cache --status
../.venv/bin/python -m engine.reference_cache --json
```

By default the helper looks for:

```text
ReClass Model/data/reference/GRCh38.fa
```

Set `RECLASS_GRCH38_FASTA` to point at a local FASTA elsewhere.

## 9. Run the scoring engine directly

```bash
cd "/Users/taranramadoss/Documents/Projects/First Project/ReClass Model"

../.venv/bin/python -c '
from engine.scoring import classify_signals
r = classify_signals({
    "revel": 0.95,
    "gnomad_af": 0.000001,
    "criteria": [
        {"criterion": "PVS1", "direction": "pathogenic", "strength": "very_strong"}
    ],
})
print(r.tier, r.total_points, r.reconstruction_hash[:12])
'
```

Expected behavior:

- High REVEL contributes computational pathogenic evidence.
- Very rare frequency contributes rarity evidence.
- Curated PVS1 very strong contributes pathogenic evidence.
- The result is deterministic and includes a reconstruction hash.

Prefer imports from `engine.scoring`. The top-level `scoring.py` is a compatibility
copy.

For day-to-day operation, prefer the `reclass` CLI (`cli.py`) over remembering
module paths:

```bash
cd "/Users/taranramadoss/Documents/Projects/First Project/ReClass Model"
../.venv/bin/python cli.py --help
../.venv/bin/python cli.py classify --revel 0.95 --gnomad-af 0.000001
../.venv/bin/python cli.py validate clingen_real_v1
../.venv/bin/python cli.py reference status
../.venv/bin/python cli.py compare clinvar_real_v1 clinvar_enriched_v1
../.venv/bin/python cli.py calibration clingen_real_v1
../.venv/bin/python cli.py report analytical-validation
../.venv/bin/python cli.py report failures clinvar_enriched_v1
```

Most commands accept `--json` for machine-readable output. The CLI is a thin,
dependency-light wrapper over existing modules; the `classify` path stays a pure
function of its inputs. Once the package is installed (`pip install -e .`), the same
commands run as `reclass ...`.

## 10. Real data currently present

Raw data is present under `ReClass Model/data/raw/`:

| Dataset | Local file | Approx local size | Role |
|---|---|---:|---|
| ClinVar GRCh38 VCF | `clinvar_GRCh38.vcf.gz` | 183 MB | Expert-reviewed labels plus legacy AF fields |
| ClinGen Evidence Repository | `clingen_erepo.tsv` | 29 MB | Expert labels plus applied ACMG criteria |
| REVEL v1.3 | `revel_all.zip` | 636 MB | Missense computational predictor |
| gnomAD v4.1 | API lookup only | n/a | Population frequency enrichment |

Generated fixtures are present under `ReClass Model/validation/fixtures/`:

- `synthetic_v1.json`
- `clingen_real_v1.json`
- `clinvar_real_v1.json`
- `clinvar_enriched_v1.json`

## 11. Rebuild real fixtures

Run from `ReClass Model/`:

```bash
PY="../.venv/bin/python"

$PY ingest/clingen_to_benchmark.py
$PY ingest/clinvar_to_benchmark.py
$PY ingest/enrich_revel.py
$PY ingest/enrich_gnomad.py 200
$PY evidence/enrich_clinvar.py
```

Notes:

- `enrich_revel.py` streams the large REVEL file from the zip.
- `enrich_gnomad.py` uses targeted public API calls; the full gnomAD release is
  too large for this local project.
- `evidence/enrich_clinvar.py` uses existing local ClinGen and ClinVar fixtures.
- The engine and validation harness remain offline/pure once fixtures exist.

## 12. Run the API and report workflow

Install component dependencies if needed:

```bash
cd "/Users/taranramadoss/Documents/Projects/First Project/ReClass Model"
../.venv/bin/python -m pip install -r api/requirements.txt
```

Run the service locally:

```bash
../.venv/bin/python -m uvicorn api.app:app --reload
```

Implemented surfaces include:

- `POST /evidence/resolve`
- `POST /classify`
- `POST /classifications`
- `GET /classifications/{id}`
- `POST /classifications/{id}/sign-off`
- `POST /reanalysis/run`
- `GET /alerts`
- `POST /alerts/{id}/state`
- `POST /validation/run` in development mode
- `GET /classifications/{id}/report/reviewer`
- `GET /classifications/{id}/report/summary`

The scalable-product feature layer adds further surfaces, including:

- Evidence workbench/coverage/curation: `GET/POST /evidence/workbench/evidence`,
  `GET /evidence/workbench/criteria`, `GET/POST /evidence/coverage`,
  `POST /evidence/curation/scan`, `GET /evidence/curation`.
- Import: `POST /evidence/import/preview` (VCF/CSV dry-run),
  `POST /evidence/import/batch` (PHI-scrubbed upstream evidence).
- Release gate: `POST /validation/release-gate`,
  `POST /validation/release-gate/{id}/approve`,
  `POST /validation/release-gate/{id}/state`, `POST /validation/release-packet`.
- Reanalysis operations: `GET /reanalysis/operator-view`, `GET/POST /reanalysis/policy`.
- Alert triage: `POST /alerts/{id}/triage`.
- Amended FHIR report: `POST /classifications/{id}/report/fhir/amended`.
- Platform/admin: `POST/GET /admin/tenants`, `GET /admin/tenants/{id}/readiness`,
  `POST/GET /webhooks/endpoints`, `POST /webhooks/events`,
  `GET /audit/retention`, `POST /audit/retention/apply`, `POST /audit/security-events`.

Deterministic FHIR Genomics export is implemented in `reporting/fhir.py` and
covered by `tests/test_fhir.py`, including draft → final → amended report state
transitions and byte-identical replayable outbound payloads; it is integration
scaffolding (a serializer plus a replayable payload envelope), not a live LIS/EHR
endpoint.

The clinician reviewer UI is served at `/reviewer/` when the API is running (the
`frontend/` static app is mounted there). Unsigned classifications remain drafts
until the sign-off endpoint records a credentialed reviewer.

Authentication and tenancy depend on the run mode:

- Production mode requires real authentication: a bearer token (JWT) or an API
  key, with RBAC enforced per route.
- An `X-Tenant-Id`-only UUID header (no auth) is allowed only in development mode.

This auth/RBAC surface, the reviewer frontend, and the deployment tooling are
proof-of-concept surfaces; they are not a validated production deployment. See
`docs/auth.md` and `docs/clinical_review.md` for details.

## 13. Operations and governance

Apply the schema when PostgreSQL is available:

```bash
cd "/Users/taranramadoss/Documents/Projects/First Project/ReClass Model"
../.venv/bin/python db/apply.py
```

Operational reanalysis helpers live in `ops/`:

- `ops/queue.py` for in-memory and DB-backed reanalysis work queues.
- `ops/scheduler.py` for provider-version/config-version trigger handling and run
  execution.
- `ops/run_report.py` for checked/unchanged/same-tier/crossed/failed/skipped run
  accounting.
- `ops/repo_guard.py` for commit hygiene checks.

Source versions, licenses, local-cache policy, and rebuild instructions are in
`ReClass Model/docs/data_governance.md`.

## 14. API auth, RBAC, and observability

The API supports real authentication and role-based access control:

- Bearer tokens (RS256/JWKS OIDC and HS256 JWT) and API keys are accepted; RBAC is
  enforced per route.
- The `X-Tenant-Id`-only header path (no credentials) is a development-only
  convenience and must not be relied on outside development.
- An append-only audit log records security-relevant actions; the audit router is
  under `api/routers/audit.py`, with helpers in `api/audit.py`, `api/auth.py`, and
  `api/authz.py`.
- Observability endpoints are exposed by `api/observability.py`:
  - `GET /health` for liveness/readiness checks.
  - `GET /metrics` for operational metrics scraping.

See `docs/auth.md` for the auth/RBAC model.

## 15. Clinician reviewer frontend

A static clinician reviewer UI lives in `ReClass Model/frontend/` and is mounted at
`/reviewer/` when the API is running. Start the API (section 12) and open
`/reviewer/` in a browser. The reviewer workflow (case review, sign-off, and
conflict handling) is described in `docs/clinical_review.md` and
`docs/conflict_handling.md`.

This frontend is a proof-of-concept surface, not a validated clinical UI.

## 16. Containerized deployment

Container/deployment tooling lives in `ReClass Model/deploy/`:

- `deploy/Dockerfile` builds the API image.
- `deploy/docker-compose.yml` brings up the API together with PostgreSQL.
- `deploy/backup.sh` performs database backups.
- `deploy/restore.sh` restores explicit backup dumps into explicit target
  databases with safety checks.

The full deployment runbook (build, run, environment variables, backups, and
restore) is in `docs/deployment.md`. This deployment is a proof-of-concept surface;
it is not a validated production deployment and nothing here is FDA-cleared or
CLIA-validated.

## 17. Additional documentation

Beyond `docs/data_governance.md`, the `ReClass Model/docs/` directory now includes:

- `clinical_review.md` — clinician reviewer workflow.
- `release_policy.md` — release/versioning policy.
- `conflict_handling.md` — handling of label/criteria conflicts.
- `operations_sop.md` — operational standard operating procedures.
- `deployment.md` — containerized deployment runbook.
- `auth.md` — authentication and RBAC model.
- `release_review.md` — release review checklist.

Clinical sign-off remains pending a credentialed human reviewer (status
`governance_reviewed_pending_credentialed_signoff`). This project is a proof of
concept; it is not a clinical device and nothing here is FDA-cleared or
CLIA-validated.
