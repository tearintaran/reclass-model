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
| Access method | Download the dated `clinvar_<YYYYMMDD>.vcf.gz` from the ClinVar GRCh38 VCF FTP/HTTPS directory into `data/raw/clinvar_GRCh38.vcf.gz` |
| Local file | `data/raw/clinvar_GRCh38.vcf.gz` (~183 MB, local-only) |
| Used by | `ingest/clinvar_to_benchmark.py` → `validation/fixtures/clinvar_real_v1.json` |
| Subset taken | `CLNREVSTAT ∈ {reviewed_by_expert_panel, practice_guideline}` + the five ACMG tiers |
| License / terms | ClinVar data are in the **public domain** (NCBI); cite ClinVar. Re-check NCBI usage policies before redistribution. |

### ClinGen Evidence Repository (expert labels + applied ACMG criteria)

| Field | Value |
|---|---|
| Source | ClinGen Evidence Repository (ERepo), an FDA-recognized variant database |
| Pinned version | ERepo bulk TSV export (record the export date when re-downloading) |
| Access method | Export the ERepo bulk TSV (the "Applied Evidence Codes (Met)" column is required) into `data/raw/clingen_erepo.tsv` |
| Local file | `data/raw/clingen_erepo.tsv` (~29 MB, local-only) |
| Used by | `ingest/clingen_to_benchmark.py` → `validation/fixtures/clingen_real_v1.json`; `evidence/enrich_clinvar.py` (direct Variation-ID match) → `validation/fixtures/clinvar_enriched_v1.json` |
| License / terms | ClinGen data are released **CC0** (public domain dedication); cite ClinGen. Re-check current terms. |

### REVEL (missense computational predictor → PP3 / BP4)

| Field | Value |
|---|---|
| Source | REVEL precomputed scores (Ioannidis et al. 2016; calibrated bins per Pejaver et al. 2022) |
| Pinned version | v1.3 |
| Access method | Download `revel_all.zip` (all precomputed scores) into `data/raw/`; `ingest/enrich_revel.py` streams it for the benchmark loci only |
| Local files | `data/raw/revel_all.zip` (~636 MB, local-only); cache `data/cache/providers/revel_cache.json` (regenerable) |
| Used by | `evidence/revel.py` (`RevelProvider`), `ingest/enrich_revel.py` |
| License / terms | REVEL is **free for non-commercial / academic use**; commercial use requires checking the authors' terms. Do **not** redistribute the score file. |

### gnomAD (population frequency → BA1 / BS1 / PM2)

| Field | Value |
|---|---|
| Source | gnomAD (Genome Aggregation Database) |
| Pinned version | v4.1, joint `faf95.popmax` (filtering allele frequency), with raw genome/exome AF fallback |
| Access method | **API only** — `ingest/enrich_gnomad.py` queries the public gnomAD GraphQL API (via `curl`) for selected benchmark loci; no bulk file is stored |
| Local file | cache `data/cache/providers/gnomad_cache.json` (regenerable; records source version, timestamp, query id, and absent-vs-failed status) |
| Used by | `evidence/gnomad.py` (`GnomadProvider`), `ingest/enrich_gnomad.py` |
| License / terms | gnomAD data are **freely available** under the gnomAD terms of use (no separate sign-off, attribution requested). Re-check current terms; respect API rate limits. |

> **Absence vs failure.** The gnomAD provider records a variant *absent* from gnomAD
> as **unknown** evidence — never allele-frequency 0 — and keeps that distinct from a
> transport failure, so a missing record never silently fabricates a PM2 signal.

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

Wire the guard as a git pre-commit hook once the repo is initialized:

```bash
# from the repository root, after `git init`:
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

> **Status:** this repository is not yet under git, so the `.gitignore` + hook are
> *forward-looking* — they activate on `git init`. The policy and the guard work today
> (the guard runs on any path list); enforcement on commit begins once git is in use.

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

# 3. Add ClinGen-applied criteria to direct ClinVar Variation-ID matches:
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

The cohort PS4 thresholds in `monitoring/reanalysis.py` were moved off placeholder
values to **published ClinGen VCEP specifications**:

- **Proband-count PS4** (`PROBAND_COUNT_AD_RULE`): ≥2 / ≥6 / ≥15 unrelated probands →
  PS4_Supporting / PS4_Moderate / PS4_Strong, applicable only when PM2 is also met.
  Originated with the ClinGen **Cardiomyopathy Expert Panel** (Kelly et al.,
  *Genet Med* 2018) and adopted unchanged by the ClinGen **Hearing Loss VCEP**
  (Oza et al., *Hum Mutat* 2018). Applied to the CMP-EP definitively-curated genes
  (MYH7, MYBPC3, TNNT2, TNNI3, TPM1, ACTC1, MYL2, MYL3) and the Hearing Loss VCEP gene
  set (GJB2, SLC26A4, MYO7A, MYO6, CDH23, TECTA, KCNQ4, COCH, USH2A).
- **Generic default** (`DEFAULT_PS4_RULE`): a conservative case-control enrichment
  rule for genes/diseases with no VCEP-specific specification, in the spirit of
  ACMG/AMP 2015 PS4 (Richards et al.) and the ClinGen SVI points framework
  (Tavtigian et al. 2020).

**Caveats (must be honored before clinical use):**

- The PM2 prerequisite the VCEP specs require is **not** re-checked inside
  `cohort_to_ps4_event`; it is supplied as a separate PM2 `EvidenceEvent` that the
  engine sums independently.
- These thresholds encode published specifications but, like `engine/config.py`, must
  be confirmed against the **current** VCEP specification version and signed off by a
  credentialed reviewer locally. They resolve nothing biological on their own.
- VCEP specification versions evolve; record the spec version you validated against in
  your deployment when you adopt these rules.
