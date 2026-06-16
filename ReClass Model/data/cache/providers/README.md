# Provider caches (local, regenerable)

Local, offline caches for the REVEL and gnomAD `EvidenceProvider`s
(`evidence/revel.py`, `evidence/gnomad.py`). These exist so repeated dev runs are
**offline after the first build** and so `fetch` is deterministic for a fixed cache
snapshot.

Nothing here is a source of truth -- every file is regenerable from the public
sources via the ingest CLIs:

- `revel_cache.json`  — `(chrom-pos-ref-alt) -> REVEL score` for the benchmark loci,
  built by `python ingest/enrich_revel.py` streaming `data/raw/revel_all.zip`.
- `gnomad_cache.json` — gnomAD GraphQL responses keyed by variant id, each entry
  recording source version, timestamp, query id, and **absent-vs-failed** status,
  built by `python ingest/enrich_gnomad.py`.
- `clinvar_revel_enriched.json` / `clinvar_gnomad_enriched.json` — enriched
  benchmark copies the CLIs emit instead of rewriting the committed fixtures.

Provider output is written **here**, never back into `validation/fixtures/*.json`,
so this job never races with the validation/storage jobs that read those fixtures.

## Commit policy (governance)

Everything in this directory is **local-only and regenerable** — do **not** commit
it. The project-root `.gitignore` ignores `data/cache/providers/*` (keeping only this
README), and `ops/repo_guard.py` actively blocks committing provider caches (reason
code `provider_cache`). Provider caches can also embed the exact coordinates queried,
so keeping them out of the repo is good hygiene as well as size control.

The committed benchmark snapshots live in `../../validation/fixtures/*.json`; the
full source/version/license register and rebuild steps are in
`../../docs/data_governance.md`.
