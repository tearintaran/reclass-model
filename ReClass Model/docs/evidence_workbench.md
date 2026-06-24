# Evidence Workbench & Batch Import

The ReClass scoring engine reproduces expert ACMG/AMP tiers well **when it is fed
complete evidence** (94.7% definitive concordance on the ClinGen ERepo fixture) and
poorly on sparse public data (5.0% on raw ClinVar, 42.4% on enriched ClinVar). The
blocker is **evidence completeness and operational governance, not scoring
thresholds.** The evidence workbench makes evidence completeness a first-class,
operable surface: a place to *capture, import, track, and report on* the criteria no
single public score encodes.

Nothing here changes the scoring math. The engine still sums only the evidence that
is actually present; every classification remains a **draft until credentialed
sign-off**.

---

## Data-boundary posture

ReClass keeps two strictly separated domains (`db/schema.sql`):

- **research** — de-identified, no patient/tenant identifier, no join back to a
  patient. Only the public `variant_key` coordinate crosses the boundary.
- **clinical** — identified, tenant-isolated by PostgreSQL row-level security (RLS).

The workbench respects that boundary:

| Surface | Domain | Tenant-scoped? | Migration table |
|---|---|---|---|
| Reviewer-entered evidence | research | no (de-identified) | `research.reviewer_evidence` |
| Evidence coverage roll-ups | clinical | yes (RLS) | `clinical.evidence_coverage` |
| Curation queue | clinical | yes (RLS) | `clinical.curation_queue` |

Reviewer-entered evidence is keyed only on `variant_key` and carries a *reviewer*
identity as curation provenance — never a patient identifier. Batch import
**scrubs PHI** before any research mapping (see below), so an identifier in an
upstream file never reaches a research table.

All DDL lives in `deploy/migrations/003_evidence_workbench.sql`.

---

## 1. Reviewer-entered structured evidence

`evidence/workbench.py` models one reviewer/pipeline-supplied criterion as a
`ReviewerEvidence` record, carrying the provenance an audit needs:

- the criterion mapping (`acmg_criterion`, `evidence_direction`, `applied_strength`),
- `source` / `source_version` / `source_url` and a content **`checksum`** (SHA-256),
- the **`access_date`** the source was read,
- the **`reviewer`** who entered it (and an optional `reviewer_credential`),
- **re-review metadata**: `status` (`active`/`expired`/`superseded`/`withdrawn`),
  `expires_at`, and `re_review_at`.

`points` is left `NULL` for strength-derived evidence so a stored classification
still reconstructs byte-for-byte. `ReviewerEvidence.to_event()` yields the engine
`EvidenceEvent` the workbench contributes; provenance rides on `event.raw`, outside
the reconstruction hash, so attribution never perturbs a tier.

The workbench is built to capture the gaps a public score does not encode (the
`WORKBENCH_CRITERIA` set): `PVS1`/LoF, `PS3`/`BS3` functional assays (OddsPath),
`PM3` phasing, `PP1`/`BS4` segregation, `PP4` phenotype/HPO, `PS4` cohort, and
`BA1`/`BS1` benign-frequency review.

### Endpoints

| Method | Path | Permission |
|---|---|---|
| `GET`  | `/evidence/workbench/criteria` | `evidence:resolve` |
| `POST` | `/evidence/workbench/evidence` | `classification:write` |
| `GET`  | `/evidence/workbench/evidence?variant_key=&status=` | `classification:read` |
| `POST` | `/evidence/workbench/evidence/{id}/status` | `classification:write` |
| `POST` | `/evidence/workbench/expire?as_of=` | `classification:write` |

`POST /evidence/workbench/expire` flips `active` entries past their `expires_at` to
`expired`, driving the periodic re-review gate. `as_of` is explicit so the operation
is deterministic.

---

## 2. Evidence-coverage roll-ups

`evidence/coverage.py` turns "which criteria does this variant actually have?" into
a measurable, sliceable surface. A `CoverageRecord` is derived by
`compute_coverage(variant_key, present_criteria, variant_class=…)`, which:

- groups criteria into evidence **categories** (`functional`, `segregation`,
  `case_control`, `lof`, …),
- compares the present categories to the **reviewable expected set** for the variant
  class, and
- marks the case **blocked** when a *blocking* category (functional, segregation,
  case-control, phasing, LoF) is missing.

`rollup(records, by)` and `summarize(records)` aggregate coverage into blocked-case
breakdowns along every dimension — **gene, VCEP, disease, variant class, provider**
(and, because the rows are tenant-scoped, per tenant). The expected-category sets are
defaults reconstructed from ACMG/AMP practice; confirm them against the validated
clinical scope before clinical use.

### Endpoints

| Method | Path | Permission |
|---|---|---|
| `POST` | `/evidence/coverage` | `classification:write` |
| `GET`  | `/evidence/coverage?by=gene\|vcep\|disease\|variant_class\|provider` | `classification:read` |

`GET /evidence/coverage` with no `by` returns the overall total/blocked counts plus a
roll-up for every dimension (one call backs the whole dashboard); with `by` it
returns that single dimension's buckets.

---

## 3. Curation queue

`evidence/curation.py` surfaces evidence problems that need a human decision rather
than more data. `scan_bundle(bundle)` inspects a resolved `EvidenceBundle` and emits
`CurationItem`s for:

- **unmatched_identity** — no source matched the variant,
- **ambiguous_identity** — more than one candidate source record matched,
- **missing_transcript** — a transcript-dependent criterion (`PVS1`/`PS1`/`PM5`/…)
  asserted without a named transcript,
- **missing_cohort_denominator** — a `PS4` claim with no cohort denominator,
- **pathogenic_benign_conflict** — both pathogenic and benign evidence present.

This job only **surfaces** the gaps. The *resolution policy* (how a conflict is
adjudicated) lives in Job 2.

### Endpoints

| Method | Path | Permission |
|---|---|---|
| `POST` | `/evidence/curation/scan` | `classification:write` |
| `GET`  | `/evidence/curation?kind=&state=` | `classification:read` |
| `POST` | `/evidence/curation/{id}/state` | `classification:write` |

`scan` resolves the variant, returns the surfaced items, and (when `enqueue: true`)
adds them to the tenant queue. Re-surfacing the same open `(variant, kind)` is a
no-op (a partial unique index), so a noisy scan cannot flood the queue.

---

## 4. Batch importers (functional / phenotype / family / cohort / lab)

`ingest/batch_import.py` loads validated upstream evidence in bulk by routing each
row through the existing `evidence.upstream` adapters — so a batch import emits the
same provenance-rich `EvidenceBundle` (criterion, strength, source, version,
checksum, access date) as a single resolve.

```python
from ingest.batch_import import import_batch

result = import_batch("functional", rows, access_date="2026-06-17")
result["report"]   # totals, called/no-call, PHI dropped, per-row entries
result["bundles"]  # de-identified EvidenceBundles, ready to persist
```

`source_kind` is one of `functional`, `phenotype`, `family`, `cohort`, `lab`; a row
may override the evidence type within the kind's allowed set (e.g. a `family` row may
be `segregation`, `de_novo`, or `phasing`).

**No raw PHI is written into research tables.** Before a row is mapped, its payload
runs through `scrub_phi`, which drops known patient-identifying fields (`mrn`,
`patient_name`, `dob`, …) and PHI containers, and records that it did so as a
`phi_fields_dropped:…` warning. The de-identified structured evidence is preserved
verbatim; the identifier never reaches a bundle.

| Method | Path | Permission |
|---|---|---|
| `POST` | `/evidence/import/batch` | `classification:write` |

---

## 5. VCF / CSV variant import (normalize, dedup, preview, dry-run)

`ingest/vcf_import.py` and `ingest/csv_import.py` bring a batch of variants to the
engine's canonical identity and report on it — **without persisting anything**. They
reuse the engine identity layer (`engine.normalize`), adding no new normalization
rules:

- **identity normalization** — multiallelic split, parsimonious trim, indel
  reference-anchored left-alignment, GRCh38 build pinned; each parsed allele gets its
  canonical `GRCh38-chrom-pos-ref-alt` key and the normalization method;
- **duplicate detection** — variants that collapse to the same canonical key;
- **evidence-resolution preview** — when a resolver is supplied, what each unique
  variant would resolve to (event counts, criteria, provider versions, warnings);
- **dry-run report** — invalid rows are recorded with a reason, never silently
  dropped.

CSV columns are matched by a small case-insensitive alias table
(`chrom`/`chromosome`/`chr`, `pos`/`position`, `ref`/`alt`), or a single
`variant_key` column.

| Method | Path | Permission |
|---|---|---|
| `POST` | `/evidence/import/preview` | `evidence:resolve` |

Request: `{ "format": "vcf"\|"csv", "content": "…", "resolve": true, "providers": [...] }`.

---

## Reviewer UI

`frontend/workbench.html` (+ `workbench.js`, `workbench.css`) is a standalone page
over these endpoints — reviewer-evidence entry, coverage roll-ups, curation triage,
and dry-run import — served alongside the reviewer app under `/reviewer/`. The bearer
token is kept in memory only; only the API base + tenant id are persisted.

---

## Storage & migration

- Persistence functions live in `storage/evidence.py`
  (`insert_reviewer_evidence`, `list_reviewer_evidence`, `upsert_coverage`,
  `enqueue_curation_item`, …).
- `evidence.workbench.WorkbenchStore` has two implementations: `InMemoryWorkbenchStore`
  (dependency-free; the default the API falls back to and what CI runs against) and
  `DbWorkbenchStore` (PostgreSQL-backed, RLS-scoped for coverage/curation).
- DDL: `deploy/migrations/003_evidence_workbench.sql`. Apply with
  `python db/apply.py` (the migration is discovered and applied in order;
  Postgres-backed tests skip cleanly when no server is available).

## Tests

- `tests/test_workbench.py` — model, in-memory store, and API (incl. tenant isolation).
- `tests/test_evidence_coverage.py` — coverage roll-ups and blocked-case logic.
- `tests/test_curation.py` — curation-gap detection.
- `tests/test_batch_import.py` — batch import + PHI scrubbing.
- `tests/test_vcf_import.py` — VCF/CSV normalization, dedup, preview, dry-run.
