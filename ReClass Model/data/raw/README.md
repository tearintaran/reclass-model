# Raw source data (local-only cache)

This directory holds the **large public source downloads** the ingest pipeline
(`ingest/*.py`) reads to build validation benchmarks. Everything here except this
README and the upstream `*.md5` checksum files is a **local-only cache**: large,
regenerable from the public source, and **never committed** to source control.

See `../../docs/data_governance.md` for the full source/version/license register and
the rebuild-from-scratch reproducibility note. The project-root `.gitignore` ignores
this directory (keeping only `README.md` + `*.md5`), and `ops/repo_guard.py` actively
blocks committing these files.

## Current contents

| File | Source | Pinned version | Approx size | Role |
|---|---|---|---:|---|
| `clinvar_GRCh38.vcf.gz` | NCBI ClinVar GRCh38 VCF | `clinvar_20260615` (see `.md5`) | 183 MB | Expert-reviewed labels + legacy AF fields |
| `clinvar_GRCh38.vcf.gz.md5` | NCBI ClinVar | — | <1 KB | Upstream checksum (**committed** for provenance) |
| `clingen_erepo.tsv` | ClinGen Evidence Repository bulk export | ERepo export (date-stamp in `docs/data_governance.md`) | 29 MB | Expert labels + applied ACMG criteria |
| `revel_all.zip` | REVEL precomputed scores | v1.3 | 636 MB | Missense predictor for PP3/BP4 |

gnomAD is **not** stored here: `ingest/enrich_gnomad.py` queries the public gnomAD
v4.1 GraphQL API for only the benchmark loci and caches responses under
`../cache/providers/` (also local-only).

## Why these are not committed

- **Size.** A single FASTA/VCF/ZIP here is tens to hundreds of MB; whole-genome
  references are multi-GB. Git is the wrong store for them.
- **Reproducibility without bloat.** The committed `*.md5` checksums plus the version
  register in `docs/data_governance.md` let anyone re-fetch the *exact* snapshot, so
  the bytes themselves need not live in the repo.
- **Licensing.** Some sources are free only under their own terms (see the register);
  redistribution via a code repo is avoided.

## Rebuilding this directory

See `../../docs/data_governance.md` → "Reproducing the build from scratch". In short:
download each source at the pinned version into this folder, verify against the
committed `*.md5`, then run the `ingest/*.py` scripts.
