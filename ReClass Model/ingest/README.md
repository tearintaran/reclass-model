# Real-Data Ingestion Pipeline

This folder turns public genomics resources into validation benchmarks for the
scoring engine. The scripts use the Python standard library; `enrich_gnomad.py`
also shells out to `curl` for targeted API requests.

Run all commands from `ReClass Model/`.

## Local raw data

Raw data currently lives in `../data/raw/`.

| Dataset | File | Approx local size | Role |
|---|---|---:|---|
| ClinVar GRCh38 VCF | `clinvar_GRCh38.vcf.gz` | 183 MB | Expert-reviewed labels plus legacy AF fields |
| ClinGen Evidence Repository | `clingen_erepo.tsv` | 29 MB | Expert labels plus applied ACMG criteria |
| REVEL v1.3 | `revel_all.zip` | 636 MB | Missense computational predictor for PP3/BP4 |
| gnomAD v4.1 | API lookup only | n/a | Population frequency for BA1/BS1/PM2 |

The full gnomAD release is too large for this local project. `enrich_gnomad.py`
queries the public gnomAD GraphQL API for only selected benchmark loci.

## Scripts

```bash
PY="../.venv/bin/python"

# 1. ClinGen ERepo -> benchmark with panel-applied ACMG criteria
$PY ingest/clingen_to_benchmark.py

# 2. ClinVar -> benchmark with labels plus available frequency fields
$PY ingest/clinvar_to_benchmark.py

# 3. Add REVEL scores to ClinVar missense SNVs
$PY ingest/enrich_revel.py

# 4. Add targeted gnomAD v4.1 popmax frequencies
$PY ingest/enrich_gnomad.py 200

# 5. Add ClinGen-applied ACMG criteria to direct ClinVar Variation ID matches,
#    with canonical-key fallback when source loci are available
$PY evidence/enrich_clinvar.py

# 6. Validate generated benchmarks and calibration
$PY validation/harness.py clingen_real_v1
$PY validation/harness.py clinvar_real_v1
$PY validation/harness.py clinvar_enriched_v1
$PY validation/compare_reports.py clinvar_real_v1 clinvar_enriched_v1
$PY validation/calibration.py clingen_real_v1
```

Outputs:

- `validation/fixtures/clingen_real_v1.json`
- `validation/fixtures/clinvar_real_v1.json`
- `validation/fixtures/clinvar_enriched_v1.json`
- `validation/reports/validation_report_clingen_real_v1.*`
- `validation/reports/validation_report_clinvar_real_v1.*`
- `validation/reports/validation_report_clinvar_enriched_v1.*`
- `validation/reports/comparison_clinvar_real_v1_vs_clinvar_enriched_v1.*`

## What each benchmark measures

### `clingen_real_v1`

This fixture feeds the engine the exact ACMG criteria each ClinGen VCEP reported
in the ERepo "Applied Evidence Codes (Met)" column.

It answers:

```text
Given the same ACMG criteria an expert panel applied, does the deterministic
point-sum reproduce the panel's final tier?
```

Current result:

- Gate: PASS
- Cases: 12,446
- Definitive concordance: 94.7%
- Serious discordance: 0.032% with 4 serious errors
- Overall exact concordance: 93.0%
- Stratification field: VCEP, stored in the fixture's `ancestry` field for
  compatibility with the current harness.

### `clinvar_real_v1`

This fixture uses high-confidence ClinVar labels, then attaches only the
machine-readable evidence currently available from this local pipeline:

- legacy ClinVar frequency fields,
- REVEL for missense SNVs where matched,
- targeted gnomAD v4.1 frequencies where queried.

It deliberately exposes the evidence-integration gap.

Current result:

- Gate: FAIL
- Cases: 21,638
- Definitive concordance: 5.0%
- Serious discordance: 0.157% with 34 serious errors
- Overall exact concordance: 19.9%

This is expected. Frequency plus a computational predictor cannot reproduce most
pathogenic or likely pathogenic expert calls because those calls often depend on
PVS1, PS3, PM3, PS2/PM6, PP1, PP4, PS4, and similar evidence not present in the
raw ClinVar fixture.

### `clinvar_enriched_v1`

This fixture starts from `clinvar_real_v1`, then uses `evidence/enrich_clinvar.py`
to match direct ClinGen ERepo records by ClinVar Variation ID. The expected labels
remain ClinVar labels; only input evidence is augmented.

Current enrichment summary:

- Cases: 21,638
- Direct ClinGen Variation ID matches: 10,649
- Canonical-key fallback matches in this fixture: 0
- Normalization failures: 2
- Unmatched: 10,989
- Cases gaining criteria: 10,649
- Total criteria added: 33,094
- Cases with warnings: 11,021
- ClinVar/ClinGen label disagreements among matched records: 30
- Multiple ClinGen match cases resolved deterministically: 2

Current result:

- Gate: FAIL
- Definitive concordance: 37.8%
- Serious discordance: 0.042% with 9 serious errors
- Overall exact concordance: 43.3%

This is a concrete evidence-integration improvement. It does not yet pass because
many ClinVar records remain unmatched or still lack complete evidence. The
canonical-key fallback path is implemented, but the current ClinGen ERepo fixture
has no usable loci in its canonical-key index, so the measured real-data lift still
comes from direct Variation ID matches.

## Limitations and upgrade path

- **Criteria parsing:** `clingen_to_benchmark.py` maps tokens such as
  `PP4_Moderate` into criterion, direction, and strength. Free-text notes and
  unrecognized tokens are dropped and counted.
- **Variant identity:** provider keys and storage-compatible canonical keys are
  implemented in `engine/normalize.py`; reference-backed left-alignment support
  exists in `engine/reference.py`, and cache/status handling exists in
  `engine/reference_cache.py`. Real-data canonical-key lift depends on upstream
  source loci and, for indels, a local GRCh38 FASTA.
- **Frequency evidence:** `clinvar_to_benchmark.py` starts with legacy
  `AF_EXAC/AF_ESP/AF_TGP` values; `enrich_gnomad.py` overwrites with targeted
  gnomAD v4.1 `faf95.popmax` where available.
- **Evidence providers:** ClinGen direct-ID enrichment plus canonical-key fallback,
  REVEL, and gnomAD are reusable providers with provenance-rich bundles. The
  remaining integration gap is broader evidence coverage beyond the current public
  source slice.
- **Ancestry:** ERepo and ClinVar do not carry per-case ancestry. The ClinGen
  fixture uses VCEP as the grouping field. True ancestry-stratified validation
  needs population-specific frequency evidence and a richer fixture schema.
- **Network:** ingestion/enrichment may need network access. Scoring and validation
  are offline once fixtures exist.
- **Licensing:** ClinVar is public domain, ClinGen ERepo is CC0, gnomAD is free
  under its terms, and REVEL is free for non-commercial/academic use. Review source
  terms before any non-learning or production use.

## Highest-value next changes

See `../../gap.md`. The next work is to refresh source snapshots under the
governance policy, supply a local GRCh38 FASTA when reference-backed indel
normalization is needed, add evidence providers for currently missing evidence
types, and clinically review/sign off the versioned config and PS4 rules before
any real-world use.
