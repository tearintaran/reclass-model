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
| `ReClass Model/evidence/` | Evidence bundle model plus ClinGen, REVEL, and gnomAD providers |
| `ReClass Model/api/` | Tenant-aware FastAPI service layer |
| `ReClass Model/reporting/` | Technical reviewer and patient-safe summary reports |
| `ReClass Model/validation/` | Fixtures, harness, failure analysis, comparison tool, calibration reports, plots, and generated reports |
| `ReClass Model/ingest/` | ClinGen, ClinVar, REVEL, and targeted gnomAD benchmark builders |
| `ReClass Model/storage/` + `ReClass Model/db/` | PostgreSQL schema, apply tool, tenant/RLS storage layer, evidence-bundle persistence, cohort counts, and reconstruction verifier |
| `ReClass Model/ops/` | Reanalysis queue, scheduler, run reports, and repo guard |
| `ReClass Model/docs/data_governance.md` | Source-version/license register, cache policy, and rebuild instructions |
| `ReClass Model/tests/` | 389 tests in the current environment |
| `plots/` | PNG diagnostics generated from validation reports |

Verified status:

- Unit/integration suite: 389 tests passing in the current environment.
- Synthetic validation: PASS, 90.5% definitive concordance.
- ClinGen real validation: PASS, 94.7% definitive concordance.
- Raw ClinVar validation: expected FAIL, 5.0% definitive concordance.
- ClinVar enriched with direct ClinGen Variation ID matches: expected FAIL, but
  improved to 37.8% definitive concordance and 9 serious errors.
- REVEL and gnomAD providers: implemented with mocked/offline unit tests and local
  provider caches under `data/cache/providers/`.
- API/reporting/sign-off: implemented and tested without requiring a live database.
- Storage/ops/reanalysis: implemented and tested against PostgreSQL in the current
  environment.
- Reference cache status helper: implemented; default GRCh38 FASTA is not bundled
  and is currently missing unless supplied locally.

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
Ran 389 tests
OK
```

PostgreSQL/RLS integration tests skip cleanly if a database is not reachable. In
the current audited environment PostgreSQL and `psycopg` are available, so the full
389-test suite executes.

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
| `synthetic_v1` | PASS | 25 | 90.5% | 0 | 92.0% |
| `clingen_real_v1` | PASS | 12,446 | 94.7% | 4 | 93.0% |
| `clinvar_real_v1` | FAIL | 21,638 | 5.0% | 34 | 19.9% |
| `clinvar_enriched_v1` | FAIL | 21,638 | 37.8% | 9 | 43.3% |

The ClinVar failures are expected. Raw ClinVar mostly contains labels plus partial
frequency/REVEL evidence, not complete structured ACMG evidence. The enriched
fixture proves that adding matched ClinGen criteria helps, but also proves that
evidence coverage is still incomplete.

Reports are written to `validation/reports/`.

## 6. Evidence providers and ClinVar enrichment

The evidence-provider layer is implemented in `ReClass Model/evidence/`.
ClinGen matching enriches ClinVar cases by ClinVar Variation ID and appends
ClinGen-applied ACMG criteria while preserving the original ClinVar expected label.
REVEL and gnomAD providers expose the same evidence used by their ingest scripts
as reusable, cached, provenance-rich `EvidenceBundle`s.

```bash
cd "/Users/taranramadoss/Documents/Projects/First Project/ReClass Model"
../.venv/bin/python evidence/enrich_clinvar.py
../.venv/bin/python -m unittest tests.test_revel_provider tests.test_gnomad_provider -v
../.venv/bin/python validation/harness.py clinvar_enriched_v1
../.venv/bin/python validation/analyze_failures.py clinvar_enriched_v1
../.venv/bin/python validation/compare_reports.py clinvar_real_v1 clinvar_enriched_v1
```

Current enrichment summary:

- ClinVar cases: 21,638
- Direct ClinGen Variation ID matches: 10,649
- Canonical-key fallback matches in the current fixture: 0
- Normalization failures: 2
- Unmatched: 10,989
- Cases gaining criteria: 10,649
- Total criteria added: 33,094
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
normalization. Whole-genome FASTA files are intentionally not committed.

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

Clinical persistence endpoints require an `X-Tenant-Id` UUID header. Unsigned
classifications remain drafts until the sign-off endpoint records a credentialed
reviewer.

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
