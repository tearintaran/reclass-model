# Provider caches (local, regenerable)

Local, offline caches for provider-backed evidence such as REVEL, gnomAD,
AlphaMissense, conservation, and gene-constraint context
(`evidence/revel.py`, `evidence/gnomad.py`, `evidence/alphamissense.py`,
`evidence/computational.py`). These exist so repeated dev runs are **offline after
the first build** and so `fetch` is deterministic for a fixed cache snapshot.

Nothing here is a source of truth -- every file is regenerable from the public
sources via the ingest CLIs:

- `revel_cache.json`  — `(chrom-pos-ref-alt) -> REVEL score` for the benchmark loci,
  built by `python ingest/enrich_revel.py` streaming `data/raw/revel_all.zip`.
- `gnomad_cache.json` — gnomAD GraphQL responses keyed by variant id, each entry
  recording source version, timestamp, query id, and **absent-vs-failed** status,
  built by `python ingest/enrich_gnomad.py`.
- `alphamissense_cache.json` — optional targeted AlphaMissense scores for benchmark
  or site-local loci.
- `conservation_cache.json` — optional per-position conservation scores.
- `gene_constraint_cache.json` — optional per-gene LOEUF/pLI/missense-Z context.
- `functional_phenotype_cache.json` — optional validated functional-assay /
  phenotype-specificity records keyed by variant id.
- `ps4_cohort_evidence.json` — PS4 evidence + cohort counts built by
  `python ingest/cohort_to_evidence.py <cohort_fixture.json>`.
- `clinvar_revel_enriched.json` / `clinvar_gnomad_enriched.json` — enriched
  benchmark copies the CLIs emit instead of rewriting the committed fixtures.

## Cache manifests (source version, checksum, access date)

Each source cache (AlphaMissense, conservation, gene constraint, and the validated
functional/phenotype source) has a byte-stable builder that writes a provenance
**manifest** sidecar `<cache>.manifest.json` recording the source, source version,
the SHA-256 **checksum** of the exact cache bytes, and the **access date** the source
was read. Rebuilding from the same inputs is byte-identical, so the
recorded checksum re-verifies. The manifest helper lives in
`evidence/cache_manifest.py` (`write_cache` / `verify_cache`).

Refresh commands (run from `ReClass Model/`; `ACCESS=$(date +%F)` for today's date):

```bash
PY="../.venv/bin/python"; ACCESS=$(date +%F)

# AlphaMissense: stream the hg38 TSV for the benchmark loci, then write cache+manifest.
$PY - <<PYEOF
from evidence.alphamissense import AlphaMissenseIndex
idx = AlphaMissenseIndex.build_from_tsv("data/raw/AlphaMissense_hg38.tsv.gz", target_keys=None)
idx.to_cache_with_manifest(access_date="$ACCESS")
PYEOF

# Conservation (phyloP) and gene constraint (gnomAD LOEUF/pLI/missense-Z):
$PY - <<PYEOF
from evidence.computational import ConservationProvider, GeneConstraintProvider
ConservationProvider.from_scores({...}).to_cache_with_manifest(access_date="$ACCESS")
GeneConstraintProvider.from_metrics({...}).to_cache_with_manifest(access_date="$ACCESS")
PYEOF

# Validated functional/phenotype source (curated rows -> cache+manifest):
$PY - <<PYEOF
from evidence.upstream import FunctionalPhenotypeCache
FunctionalPhenotypeCache.build_from_rows(rows).to_cache_with_manifest(access_date="$ACCESS")
PYEOF

# Verify any cache against its recorded manifest checksum:
$PY -c "from evidence import cache_manifest as m; \
        print(m.verify_cache('data/cache/providers/alphamissense_cache.json'))"
```

The `<cache>.manifest.json` sidecars are local/regenerable like the caches and are
ignored by the same `data/cache/providers/*` rule.

Provider output is written **here**, never back into `validation/fixtures/*.json`,
so cache regeneration never disturbs the committed benchmark fixtures or the
storage layer that reads them.

## Commit policy (governance)

Everything in this directory is **local-only and regenerable** — do **not** commit
it. The project-root `.gitignore` ignores `data/cache/providers/*` (keeping only this
README), and `ops/repo_guard.py` actively blocks committing provider caches (reason
code `provider_cache`). Provider caches can also embed the exact coordinates queried,
so keeping them out of the repo is good hygiene as well as size control.

The committed benchmark snapshots live in `../../validation/fixtures/*.json`; the
full source/version/license register and rebuild steps are in
`../../docs/data_governance.md`.
