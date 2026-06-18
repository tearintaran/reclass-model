# Data governance & source hygiene

This document makes the project's external-source use **reproducible** and
**legally/operationally clear**. It answers four questions an auditor or a new
agent will ask:

1. Where did each piece of external data come from, at what version, under what
   license, and how was it obtained? → [Source register](#1-source-register)
2. Which files are source, which are local cache, which are generated artifacts, and
   which are prohibited (private/clinical)? → [File classification](#2-file-classification)
3. How is "don't commit the wrong thing" enforced? → [Commit hygiene](#3-commit-hygiene)
4. How do I rebuild everything from scratch? → [Reproducing the build](#4-reproducing-the-build-from-scratch)

A fifth section records the [clinical-calibration provenance](#5-clinical-calibration-provenance)
for the PS4 cohort rules implemented in `monitoring/reanalysis.py`.

> Scope note: this engine is **decision support**. No classification it produces may
> be released clinically without credentialed human sign-off, and every external
> source below must be re-reviewed under its own current terms before any non-research
> or production use.

**Last governance review:** 2026-06-16. Outcome: source terms were re-reviewed for
ClinVar, ClinGen, REVEL, and gnomAD; the local raw snapshots and committed fixtures
were checksummed; no public fixture was intentionally refreshed in this review.

---

## 1. Source register

All sizes/versions reflect the snapshot currently in `data/`. Re-verify the version
strings and license terms at the source before reuse — upstream releases change.

### ClinVar (expert labels)

| Field | Value |
|---|---|
| Source | NCBI ClinVar, GRCh38 VCF release |
| Pinned version | `clinvar_20260615.vcf.gz` (date-stamped weekly release; see `data/raw/clinvar_GRCh38.vcf.gz.md5`) |
| Upstream checksum | `e19ba3d834e402f65cdc42293dcceace` (committed alongside the file) |
| Local SHA-256 | `10d86b892aae1f035e1950844e13fb039dad50be1087a0d1445c60d29191a342` |
| VCF header | `##fileDate=2026-06-15`, `##reference=GRCh38` |
| Access / review date | 2026-06-16 |
| Access method | Download the dated `clinvar_<YYYYMMDD>.vcf.gz` from the ClinVar GRCh38 VCF FTP/HTTPS directory into `data/raw/clinvar_GRCh38.vcf.gz` |
| Local file | `data/raw/clinvar_GRCh38.vcf.gz` (192,127,389 bytes, local-only) |
| Used by | `ingest/clinvar_to_benchmark.py` → `validation/fixtures/clinvar_real_v1.json` |
| Subset taken | `CLNREVSTAT ∈ {reviewed_by_expert_panel, practice_guideline}` + the five ACMG tiers |
| License / terms | NCBI molecular data usage policy places no NCBI restrictions on use/distribution but notes submitter/third-party rights may exist; cite ClinVar and re-check NCBI policy before redistribution. |

### ClinGen Evidence Repository (expert labels + applied ACMG criteria)

| Field | Value |
|---|---|
| Source | ClinGen Evidence Repository (ERepo), an FDA-recognized variant database |
| Pinned version | Local ERepo bulk TSV snapshot; export date is not embedded in the TSV and must be recorded at next download |
| Local SHA-256 | `c7ec8e76d336988029f12d500636d6e4a4964c44e6b37e4bcef682c0ecd0312c` |
| Access / review date | 2026-06-16 |
| Access method | Export the ERepo bulk TSV (the "Applied Evidence Codes (Met)" column is required) into `data/raw/clingen_erepo.tsv` |
| Local file | `data/raw/clingen_erepo.tsv` (29,941,482 bytes, local-only) |
| Used by | `ingest/clingen_to_benchmark.py` → `validation/fixtures/clingen_real_v1.json`; `evidence/enrich_clinvar.py` (direct Variation-ID match) → `validation/fixtures/clinvar_enriched_v1.json` |
| License / terms | ClinGen curated content is released under **CC0 1.0**; ClinGen requests attribution and access date. Re-check current terms before production use. |

### REVEL (missense computational predictor → PP3 / BP4)

| Field | Value |
|---|---|
| Source | REVEL precomputed scores (Ioannidis et al. 2016; calibrated bins per Pejaver et al. 2022) |
| Pinned version | v1.3; archive member `revel_with_transcript_ids`, dated 2021-05-03 |
| Local SHA-256 | `815801d832e4ab76922dddfd901d7f24a755df57078a7138aa14f615da86a8a2` |
| Access / review date | 2026-06-16 |
| Access method | Download `revel_all.zip` (all precomputed scores) into `data/raw/`; `ingest/enrich_revel.py` streams it for the benchmark loci only |
| Local files | `data/raw/revel_all.zip` (667,102,707 bytes, local-only); cache `data/cache/providers/revel_cache.json` (regenerable) |
| Used by | `evidence/revel.py` (`RevelProvider`), `ingest/enrich_revel.py` |
| License / terms | REVEL scores are freely available for non-commercial use; other uses require contacting the REVEL maintainer. Do **not** redistribute the score file. |

### gnomAD (population frequency → BA1 / BS1 / PM2)

| Field | Value |
|---|---|
| Source | gnomAD (Genome Aggregation Database) |
| Pinned version | v4.1, joint `faf95.popmax` (filtering allele frequency), with raw genome/exome AF fallback |
| Access / review date | 2026-06-16 |
| Access method | **API only** — `ingest/enrich_gnomad.py` queries the public gnomAD GraphQL API (via `curl`) for selected benchmark loci; no bulk file is stored |
| Local file | cache `data/cache/providers/gnomad_cache.json` (regenerable; records source version, timestamp, query id, and absent-vs-failed status) |
| Used by | `evidence/gnomad.py` (`GnomadProvider`), `ingest/enrich_gnomad.py` |
| License / terms | gnomAD summary data are released for broad scientific use; attribution is requested. Re-check current terms, respect API limits, and do not treat gnomAD as a screened healthy-control cohort. |

> **Absence vs failure.** The gnomAD provider records a variant *absent* from gnomAD
> as **unknown** evidence — never allele-frequency 0 — and keeps that distinct from a
> transport failure, so a missing record never silently fabricates a PM2 signal.

### AlphaMissense / conservation / gene constraint (computational extensions)

| Field | Value |
|---|---|
| Source | AlphaMissense precomputed scores; phyloP/phastCons-style conservation scores; gnomAD gene constraint metrics |
| Pinned version | No bulk source snapshot is committed in this repo; thresholds are versioned in `engine/configs/computational_ext_v1.json` |
| Access / review date | 2026-06-17 project-alignment pass recorded code support only; source terms must be reviewed before any bulk cache is built or used clinically |
| Access method | Future targeted cache builders should stream/import only required benchmark or site-local loci/genes into `data/cache/providers/` |
| Local files | Optional local-only caches such as `alphamissense_cache.json`, `conservation_cache.json`, and `gene_constraint_cache.json` under `data/cache/providers/` |
| Used by | `evidence/alphamissense.py`, `evidence/computational.py`, `engine/configs/computational_ext_v1.json` |
| License / terms | Review each source's current license and redistribution terms before production or clinical use. Do not commit bulk predictor, conservation, or constraint files. |

### Source-terms review log

| Date | Source | Terms reviewed | Outcome |
|---|---|---|---|
| 2026-06-16 | ClinVar / NCBI | NCBI ClinVar FTP primer and NCBI data usage policies | OK for research fixture use with citation; re-review required before redistribution or production use. |
| 2026-06-16 | ClinGen | ClinGen citing and terms-of-use page | OK for curated-content use under CC0 with attribution and access date. |
| 2026-06-16 | REVEL | REVEL downloads page | Restricted to non-commercial use unless separate permission is obtained; no redistribution of score file. |
| 2026-06-16 | gnomAD | gnomAD browser/data-use information and open-data registry | OK for research summary-frequency use with attribution and API respect; confirm terms before production use. |

### Committed fixture snapshot register

No fixture was intentionally refreshed in the 2026-06-16 governance pass. The
2026-06-17 project-alignment pass re-recorded the current working snapshot hashes
below so they match the files and reports currently in this tree.

| Fixture | SHA-256 | Regeneration command |
|---|---|---|
| `validation/fixtures/clingen_real_v1.json` | `1546cd7188a94f9e17cae77e1320f47d63e9d34d22b5d97c2906b2fcaf0f4a6a` | `$PY ingest/clingen_to_benchmark.py` |
| `validation/fixtures/clinvar_real_v1.json` | `59f5098e612c44a118587bb659efd673feba13045425f192c224ab84791a0f75` | `$PY ingest/clinvar_to_benchmark.py` |
| `validation/fixtures/clinvar_enriched_v1.json` | `96c994d95e6504884d73b45e08fe6916ea326369dfd7fcbb8b94a56045a5c837` | `$PY evidence/enrich_clinvar.py` after ClinVar and ClinGen fixtures are present |
| `validation/fixtures/synthetic_v1.json` | `6fd2fb8320e5353123a7c7e88e9dc581e928190505b03b483b5a97c0fd51a6e8` | `$PY validation/build_fixtures.py` |

When intentionally refreshing a fixture, update this table in the same commit as the
fixture change and include: source version, raw/source checksum, access date,
regeneration command, and the new fixture SHA-256.

---

## 2. File classification

Every data path falls into exactly one class. The first column is what a new agent
needs to know before touching a file.

| Class | Where | Committed? | Notes |
|---|---|:--:|---|
| **Source code / docs** | `engine/`, `evidence/`, `ingest/`, `monitoring/`, `ops/`, `storage/`, `db/`, `validation/*.py`, `*.md` | ✅ yes | The system of record for logic. |
| **Source checksums** | `data/raw/*.md5` | ✅ yes | Tiny; pin the raw snapshot for reproducibility. |
| **Benchmark fixtures** | `validation/fixtures/*.json` | ✅ yes | Small, generated **once** from raw sources then committed as a stable validation snapshot (so CI/validation runs offline and deterministically). Regenerate only when intentionally refreshing a benchmark. |
| **Raw source data** | `data/raw/*` (VCF/TSV/ZIP) | ❌ no | Large local-only cache; rebuild from the source register. |
| **Reference genome** | `data/reference/*.fa`, `*.fai` | ❌ no | Multi-GB GRCh38 FASTA cache; see `data/reference/README.md`. |
| **Provider caches** | `data/cache/providers/*.json` | ❌ no | Regenerable via `ingest/*`; may embed queried coordinates. |
| **Enriched benchmark copies** | `data/cache/providers/clinvar_*_enriched.json` | ❌ no | Provider output; never written back into `validation/fixtures/`. |
| **Diagnostic plots** | `plots/*.png` | ❌ no | Regenerable via `validation/plots.py`. |
| **Validation/comparison/failure reports** | `validation/reports/*` | ❌ no | Regenerable via `validation/harness.py` / `analyze_failures.py` / `compare_reports.py`. |
| **Private / clinical data** | `**/data/private/`, PHI/MRN-shaped files | 🚫 prohibited | Identified clinical data lives **only** in PostgreSQL under RLS — never in the repo. |

**Why fixtures are committed but their inputs are not:** the fixtures are small,
deterministic distillations that the test/validation gates depend on; committing them
keeps the gates fast and offline. Their multi-hundred-MB inputs add no logic and are
fully described by the source register + checksums, so they stay out of git.

---

## 3. Commit hygiene

Two layers enforce "never commit a large/raw/private file", mirroring the two-layer
pattern already used for alerts (app guard + schema CHECK):

1. **`.gitignore`** (project root) — passive: git won't stage ignored paths. It
   ignores `data/raw/*`, `data/reference/*`, `data/cache/providers/*`, `plots/`,
   `validation/reports/`, virtualenvs, caches, and PHI-shaped names, while keeping the
   READMEs, `*.md5` checksums, and `validation/fixtures/*.json`.
2. **`ops/repo_guard.py`** — active: a stdlib-only commit guard that scans paths and
   fails (exit 1) on any prohibited file, with a deterministic reason code
   (`large_fasta`, `raw_archive`, `provider_cache`, `private_clinical`, `oversized`).
   It defeats `git add -f` and the oversized catch-all (>5 MiB) catches anything that
   slips past name rules. Unit-tested in `tests/test_ops.py`.

This repository is initialized. The guard is wired as `.git/hooks/pre-commit` and
can be reinstalled manually with:

```bash
cat > .git/hooks/pre-commit <<'EOF'
#!/usr/bin/env bash
exec "$(git rev-parse --show-toplevel)/.venv/bin/python" \
     "ReClass Model/ops/repo_guard.py" --staged --repo-root "$(git rev-parse --show-toplevel)"
EOF
chmod +x .git/hooks/pre-commit
```

You can also run it ad hoc against explicit paths:

```bash
../.venv/bin/python ops/repo_guard.py data/raw/revel_all.zip   # -> raw_archive, exit 1
```

> **Status:** active as of 2026-06-16. The pre-commit hook invokes
> `ops/repo_guard.py --staged` from the repository root, so forced-add raw archives,
> provider caches, FASTA files, PHI-shaped files, and oversized blobs are blocked at
> commit time.

---

## 4. Reproducing the build from scratch

Goal: from an empty `data/` (only the committed READMEs + `*.md5`), rebuild every
local cache and fixture. Run from `ReClass Model/`.

```bash
PY="../.venv/bin/python"

# 0. Re-fetch the raw sources at their pinned versions (see the source register):
#    - data/raw/clinvar_GRCh38.vcf.gz        (verify against the committed .md5)
#    - data/raw/clingen_erepo.tsv
#    - data/raw/revel_all.zip
#    (gnomAD needs no file: it is queried live by enrich_gnomad.py)
md5sum -c data/raw/clinvar_GRCh38.vcf.gz.md5    # confirm the ClinVar snapshot

# 1. Build the expert-criteria and label benchmarks from raw sources:
$PY ingest/clingen_to_benchmark.py     # -> validation/fixtures/clingen_real_v1.json
$PY ingest/clinvar_to_benchmark.py     # -> validation/fixtures/clinvar_real_v1.json

# 2. Rebuild provider caches (local, offline after first run):
$PY ingest/enrich_revel.py             # -> data/cache/providers/revel_cache.json (+ enriched copy)
$PY ingest/enrich_gnomad.py 200        # -> data/cache/providers/gnomad_cache.json (live API)

# 3. Add ClinGen-applied criteria by direct ClinVar Variation ID first,
#    with canonical-key and genomic-HGVS fallbacks when identity fields exist:
$PY evidence/enrich_clinvar.py         # -> validation/fixtures/clinvar_enriched_v1.json

# 4. Regenerate reports + plots (artifacts; not committed):
$PY validation/harness.py clingen_real_v1
$PY validation/harness.py clinvar_real_v1
$PY validation/harness.py clinvar_enriched_v1
$PY validation/analyze_failures.py clinvar_enriched_v1
$PY validation/compare_reports.py clinvar_real_v1 clinvar_enriched_v1
$PY validation/plots.py
```

Determinism: scoring and validation are pure/offline once fixtures exist (only step 0
and the gnomAD query in step 2 need network). The same source versions reproduce the
same fixtures and the same `reconstruction_hash` per classification.

Optional GRCh38 reference (for reference-anchored indel left-alignment): place a FASTA
at `data/reference/GRCh38.fa` or set `RECLASS_GRCH38_FASTA`; never commit it (see
`data/reference/README.md`). Check status with
`$PY -m engine.reference_cache --status`.

---

## 5. Clinical-calibration provenance

The cohort PS4 thresholds in `monitoring/reanalysis.py` were re-reviewed against
current ClinGen CSpecs on 2026-06-16:

- **Hearing Loss autosomal-dominant proband-count PS4** (`PROBAND_COUNT_AD_RULE`):
  2 / 6 / 15 unrelated probands -> PS4_Supporting / PS4_Moderate / PS4_Strong,
  applicable only when PM2_Supporting is also met, per ClinGen Hearing Loss CSpec
  v2.0.0. Encoded only for `COCH`, `KCNQ4`, and `MYO6`; recessive genes such as
  `GJB2` fall back to the default.
- **Cardiomyopathy genes**: no proband-count shortcut is encoded. Current
  cardiomyopathy CSpecs, including ACTC1 v1.0.0 released 2024-04-22, require
  case/control odds-ratio 95% confidence-interval lower-bound thresholds for PS4.
  `PS4OddsRatioRule` implements this denominator-aware mode when case/control
  totals are supplied. Bare proband counts yield no PS4 event for cardiomyopathy
  genes, and the OR/CI threshold bins remain reviewable defaults pending
  credentialed current-spec sign-off.
- **Generic default** (`DEFAULT_PS4_RULE`): a conservative case-control enrichment
  fallback for genes/diseases with no encoded VCEP-specific rule.

**Caveats (must be honored before clinical use):**

- The PM2 prerequisite the VCEP specs require is **not** re-checked inside
  `cohort_to_ps4_event`; it is supplied as a separate PM2 `EvidenceEvent` that the
  engine sums independently.
- These thresholds encode governance-reviewed specifications but, like
  `engine/config.py`, must be signed off by a credentialed reviewer locally before
  clinical use. They resolve nothing biological on their own.
- VCEP specification versions evolve; record the spec version you validated against
  in your deployment when you adopt these rules.
