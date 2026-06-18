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
| AlphaMissense / conservation / gene constraint | optional local caches only | n/a | Computational extensions; no bulk source files are committed |

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

# 5. Add ClinGen-applied ACMG criteria by direct ClinVar Variation ID first, with
#    Allele-ID, canonical-key, genomic-HGVS, SPDI, and MANE-transcript fallbacks
#    when those identity fields exist
$PY evidence/enrich_clinvar.py

# 5b. (optional) PS4 evidence + cohort counts from a case-control cohort fixture.
#     Writes data/cache/providers/ps4_cohort_evidence.json.
$PY ingest/cohort_to_evidence.py path/to/cohort_fixture.json

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
- Stratification fields: the VCEP/expert-panel grouping is stored in
  the dedicated `vcep_group` field, distinct from the true ancestry/population field
  `population` (None for ERepo, which carries no per-case ancestry). The legacy
  `ancestry` field is retained as a back-compatible alias of `vcep_group` for the
  current harness. Each case also carries a GRCh38 SNV/MNV `locus` parsed from the
  genomic HGVS, used by the canonical-key fallback matcher.

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
to match ClinGen ERepo records by direct ClinVar Variation ID first, then by
weaker canonical-key and genomic-HGVS fallback routes. The expected labels remain
ClinVar labels; only input evidence is augmented.

Current enrichment summary:

- Cases: 21,638
- Direct ClinGen Variation ID matches: 10,649
- Canonical SNV-key fallback matches in this fixture: 940
- Reference-backed indel-key fallback matches: 0
- Genomic HGVS fallback matches: 381
- Normalization failures: 2
- Unmatched: 9,668
- Cases gaining criteria: 11,970
- Total criteria added: 37,873
- Cases with warnings: 9,700
- ClinVar/ClinGen label disagreements among matched records: 30
- Multiple ClinGen match cases resolved deterministically: 2

Current result:

- Gate: FAIL
- Definitive concordance: 42.4%
- Serious discordance: 0.028% with 6 serious errors
- Overall exact concordance: 46.6%

This is a concrete evidence-integration improvement. It does not yet pass because
many ClinVar records remain unmatched or still lack complete evidence. The
canonical-key fallback path is now populated: the ClinGen ERepo fixture exposes
usable loci, so SNV canonical-key matching adds 940 matches on top of the direct
Variation ID matches. Genomic-HGVS matching adds 381 additional matches when the
ClinGen record carries a GRCh38 genomic HGVS token. Native reference-backed
indel-key matching is implemented but yields 0 additional matches on this real
fixture, because its indels have no repeat-shifted spelling collisions for
left-alignment to resolve.

## Limitations and upgrade path

- **Criteria parsing:** `clingen_to_benchmark.py` maps tokens such as
  `PP4_Moderate` into criterion, direction, and strength. Free-text notes and
  unrecognized tokens are dropped and counted.
- **Variant identity:** provider keys and storage-compatible canonical keys are
  implemented in `engine/normalize.py`; reference-backed left-alignment support
  exists in `engine/reference.py`, and cache/status handling exists in
  `engine/reference_cache.py`. Real-data canonical-key lift depends on upstream
  source loci and, for indels, a local GRCh38 FASTA. Beyond Variation ID and the
  canonical/genomic-HGVS fallbacks, the matcher also supports **ClinVar Allele ID**,
  **NCBI SPDI** (parsed in `ingest/hgvs.py`, resolved to a canonical genomic key),
  and **MANE-transcript + coding-HGVS** identity routes (`engine/normalize.py`
  transcript helpers), each with explicit ambiguity accounting — a key that maps to
  multiple non-equivalent records imports nothing and is flagged, never silently
  resolved.
- **Transcript identity:** ingested cases carry a MANE Select / RefSeq `transcript`
  block (parsed from the coding HGVS in `ingest/hgvs.py`), the evidence providers
  carry it into `EvidenceBundle.transcript`, and it is surfaced through the API
  schemas, reviewer review packets, and clinical/FHIR reports.
- **Upstream evidence adapters:** `evidence/upstream.py` adds provenance-rich adapters
  for de novo (PS2/PM6), phasing (PM3/BP2), segregation (PP1/BS4), phenotype (PP4),
  functional assay (PS3/BS3), disease mechanism (PP2/BP1), and case-control (PS4).
  Each records source version, a content checksum, and an access date, and emits an
  explicit *absent* / *no-call* record when evidence is missing rather than guessing.
- **PS4 cohort counts:** `ingest/cohort_to_evidence.py` turns a case-control cohort
  fixture into PS4 evidence carrying the denominator + case/control counts on
  `EvidenceBundle.cohort_counts`.
- **Frequency evidence:** `clinvar_to_benchmark.py` starts with legacy
  `AF_EXAC/AF_ESP/AF_TGP` values; `enrich_gnomad.py` overwrites with targeted
  gnomAD v4.1 `faf95.popmax` where available.
- **Evidence providers:** ClinGen direct-ID enrichment plus canonical-key and
  genomic-HGVS fallbacks, REVEL, gnomAD, AlphaMissense, conservation,
  gene-constraint, and extended structured-evidence providers are reusable
  providers with provenance-rich bundles. The remaining integration gap is
  validated source population beyond the current public source slice.
- **Ancestry:** ERepo and ClinVar do not carry per-case ancestry. The ClinGen
  fixture uses VCEP as the grouping field. True ancestry-stratified validation
  needs population-specific frequency evidence and a richer fixture schema.
- **Network:** ingestion/enrichment may need network access. Scoring and validation
  are offline once fixtures exist.
- **Licensing:** ClinVar is public domain, ClinGen ERepo is CC0, gnomAD is free
  under its terms, and REVEL is free for non-commercial/academic use. Review source
  terms for AlphaMissense, conservation, gene-constraint, and any other added
  source before cache building, redistribution, clinical, or production use.

## Highest-value next changes

See `../../gap.md`. A local GRCh38 FASTA is now installed (so reference-backed
indel normalization runs locally) and the ClinGen fixture exposes usable loci for
SNV canonical-key matching. The next work is to refresh source snapshots under the
governance policy when fixtures change, populate the structured providers from
validated upstream sources, document computational cache rebuilds, and obtain
credentialed clinical sign-off of the versioned config and PS4 rules before any
real-world use.
