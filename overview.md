# ReClass Reference Model

Audience: medical practitioners, clinical laboratory professionals, geneticists,
variant scientists, and researchers who want to understand what ReClass can do,
what information it accepts, how it uses that information, and what it produces.
This is not a programmer guide.

## Clinical Status

ReClass is a local proof of concept for auditable, reproducible variant
reclassification. It is decision support only.

It is not FDA-cleared, CLIA-validated, or suitable for autonomous patient
reporting. A qualified human reviewer remains responsible for evidence assessment,
interpretation, sign-off, and any clinical release. ReClass does not diagnose,
recommend treatment, estimate penetrance, or make patient-management decisions.

## What ReClass Does

ReClass takes structured variant evidence, applies a deterministic ACMG/AMP-style
point model, and returns one of the standard five tiers:

- Pathogenic
- Likely Pathogenic
- Variant of Uncertain Significance (VUS)
- Likely Benign
- Benign

The output is not just a tier. Each result is an auditable receipt that records the
evidence used, the points contributed by each item, source versions, warnings,
review status, engine/configuration version, and a reconstruction hash that allows
the classification to be verified later.

## Current Capabilities

ReClass currently supports:

- Deterministic ACMG/AMP-style scoring from structured criteria and selected source
  signals.
- A versioned scoring configuration with reviewable VCEP/gene/disease overrides.
- Canonical variant identity using both source-style provider keys such as
  `1-100-A-G` and storage-compatible keys such as `GRCh38-1-100-A-G`.
- Reference-free variant normalization and reference-backed indel left-alignment
  when a local GRCh38 FASTA is supplied.
- ClinGen Evidence Repository criteria through a reusable evidence provider.
- REVEL missense computational evidence through a reusable evidence provider.
- gnomAD allele-frequency evidence through a reusable evidence provider with local
  caching.
- ClinVar-to-ClinGen enrichment by direct ClinVar Variation ID, with
  canonical-key fallback support when source loci are available.
- Cohort-count PS4 evidence using published ClinGen VCEP proband-count rules for
  supported gene sets, with a conservative fallback elsewhere.
- Evidence bundles that preserve provider versions, source records, warnings,
  match type, and raw provenance.
- Tenant-aware persistence for classifications, evidence events, evidence bundles,
  cohort counts, reanalysis events, alerts, and sign-off state.
- A reviewer workflow in which persisted classifications remain drafts until
  credentialed sign-off.
- Technical reviewer reports and patient-safe summary reports.
- Continuous reanalysis support with queueing, run reports, same-tier audit events,
  and tier-crossing alerts.
- Validation on synthetic, ClinGen, raw ClinVar, and ClinVar-plus-ClinGen
  benchmarks.
- Failure-analysis, before/after comparison, calibration, and diagnostic plot
  reports.
- Source-governance documentation for public-data versions, licenses, cache policy,
  and reproducibility.

## What ReClass Does Not Do

ReClass does not currently provide:

- A clinician-facing web application.
- Production deployment, authentication/authorization integration, operational
  monitoring, backups, or SOPs.
- Local clinical validation of the reconstructed configuration or PS4 rules.
- Independent assessment of papers, functional assays, segregation evidence,
  phenotype match, or expert assertions.
- Broad automated evidence coverage beyond the current ClinGen, REVEL, gnomAD, and
  cohort-count slice.
- General coverage for splice, CNV, structural-variant, repeat-expansion,
  mitochondrial, non-coding, and complex-indel interpretation.

## Input Model

ReClass can operate on benchmark records or future clinical/research records. A
record may contain direct ACMG criteria, source signals that can be converted into
criteria, or both.

| Input | What It Means | How ReClass Uses It |
|---|---|---|
| Variant coordinates | Chromosome, position, reference allele, alternate allele, and genome build | Normalizes the variant, creates source/provider keys, links evidence across sources, and stores de-identified variant evidence |
| ClinVar Variation ID | A ClinVar source identifier | Used for direct ClinVar-to-ClinGen evidence matching when available |
| Gene/disease/VCEP context | Gene symbol, disease context, expert-panel context, or variant key | Selects reviewable VCEP/gene/disease configuration overrides when present |
| Structured ACMG/AMP criteria | Examples: PVS1, PS3, PM2, PM3, PP3, BA1, BS1, BP4, with direction and strength | Scored directly by the point model |
| REVEL score | Missense pathogenicity score for single-nucleotide missense variants | Converted to PP3 or BP4 according to calibrated bins; indeterminate scores are recorded without adding points |
| gnomAD frequency | Preferably `joint.faf95.popmax`, with genome/exome AF fallback | Converted to BA1, BS1, or PM2-style frequency evidence when thresholds are met |
| ClinGen Evidence Repository criteria | Expert-panel-applied ACMG criteria tied to source IDs or loci | Added as structured criteria when a case matches ClinGen evidence |
| Cohort counts | De-identified counts by variant and group, such as affected probands or case/control enrichment | Can generate PS4 evidence when configured cohort thresholds are met |
| Provenance metadata | Source, version, query ID, source records, match method, warnings, and review status | Preserved for audit, reporting, validation, and reconstruction |

Large genome reference files are not bundled. A local GRCh38 FASTA can be supplied
for reference-backed normalization.

## Supported Evidence Sources

| Source | Current Use | Important Behavior |
|---|---|---|
| ClinGen Evidence Repository | Transfers expert-panel-applied ACMG criteria onto matching variants | Direct ClinVar Variation ID matching is used first; canonical-key matching is available when loci exist; missing IDs, failed normalization, no match, duplicate match, and label disagreement are reported |
| REVEL | Provides computational evidence for missense SNVs | High scores can contribute PP3; low scores can contribute BP4; scores in indeterminate ranges do not add criteria |
| gnomAD v4.1 | Provides allele-frequency evidence | Uses popmax FAF when available; falls back to genome/exome AF with warnings; absence from gnomAD is unknown evidence, not allele frequency zero |
| ClinVar | Provides public benchmark labels and some frequency fields | Used to measure how sparse public evidence behaves; labels are not treated as biological ground truth |
| De-identified cohort counts | Provides PS4-style enrichment evidence | Published VCEP proband-count rules are used for supported gene sets; PM2 evidence is supplied separately |

## How ReClass Uses Inputs

1. **Variant identity is normalized.** ReClass maps source-specific coordinates into
   a canonical identity format. Provider keys omit the build token; storage keys
   include it.
2. **Evidence is gathered.** Supplied criteria and evidence-provider results are
   assembled into an evidence bundle.
3. **Signals become criteria where possible.** REVEL, gnomAD, and cohort-count
   signals can become ACMG/AMP-style criteria under configured thresholds.
4. **Criteria are scored.** Each criterion contributes signed points according to
   its direction and strength.
5. **Points become a tier.** The net point total maps to the five-tier
   classification scale.
6. **Provenance is attached.** Source versions, warnings, source records, match
   details, configuration version, and reconstruction hash are included.
7. **Human review controls release.** Persisted classifications are drafts until a
   credentialed reviewer signs off.

The scoring core is deterministic. For the same evidence and same engine/config
version, it returns the same tier and reconstruction hash.

## Scoring Model

ReClass uses a Tavtigian/ClinGen SVI-style point model.

| Evidence strength | Pathogenic points | Benign points |
|---|---:|---:|
| Supporting | +1 | -1 |
| Moderate | +2 | -2 |
| Strong | +4 | -4 |
| Very Strong | +8 | n/a |
| Stand-alone benign, such as BA1 | n/a | benign override |

| Net result | Tier |
|---|---|
| Pathogenic-level positive evidence | Pathogenic |
| Likely-pathogenic-level positive evidence | Likely Pathogenic |
| Neither sufficient pathogenic nor benign evidence | VUS |
| Likely-benign-level negative evidence | Likely Benign |
| Benign-level negative evidence or BA1 stand-alone | Benign |

The default configuration is reviewable and versioned, but it is reconstructed from
published guidance and must be clinically reviewed before use in patient care.

## Outputs

For an individual classification, ReClass can produce:

- Predicted tier.
- Total points.
- Per-criterion contribution table.
- Evidence direction, strength, points, source, and source version.
- Stand-alone overrides, such as BA1.
- Provider versions and source records.
- Warnings and blocking normalization problems.
- Normalized/canonical identity.
- Engine/configuration version.
- Reconstruction hash.
- Draft or signed-off release status.

For human review, ReClass can produce:

- A technical reviewer report showing identity, evidence grouped by source,
  criteria, strengths, points, warnings, source records, prior classifications,
  reanalysis history, and alerts.
- A patient-safe summary report that avoids treatment or management directives.

For operations and validation, ReClass can produce:

- Validation reports in Markdown and JSON.
- Failure-analysis reports.
- Before/after comparison reports.
- Calibration reports by VCEP, gene, and disease group.
- Diagnostic plots.
- Reanalysis run reports showing checked, unchanged, same-tier changed,
  tier-crossing, failed, and skipped cases.
- Tier-crossing alerts and same-tier audit history.

## Current Validation Evidence

These results measure agreement with public reference labels. They are useful for
understanding reproducibility and evidence completeness, but they are not proof of
biological truth.

| Benchmark | Cases | Gate | Definitive Concordance | Serious Discordance | Overall Exact Concordance | Interpretation |
|---|---:|---|---:|---:|---:|---|
| `synthetic_v1` | 25 | PASS | 90.5% | 0 cases | 92.0% | Confirms scoring and harness behavior |
| `clingen_real_v1` | 12,446 | PASS | 94.7% | 4 cases | 93.0% | Expert-applied ClinGen criteria mostly reproduce expert-panel tiers |
| `clinvar_real_v1` | 21,638 | FAIL | 5.0% | 34 cases | 19.9% | Sparse public evidence is not enough for most ClinVar labels |
| `clinvar_enriched_v1` | 21,638 | FAIL | 37.8% | 9 cases | 43.3% | Adding matched ClinGen criteria substantially improves concordance but does not solve missing evidence |

The key scientific lesson is that the same scoring engine performs well when it
receives complete expert-applied evidence and poorly when the evidence is sparse.
The main blocker is evidence completeness and evidence quality, not threshold
loosening.

## ClinVar Enrichment Result

The current enriched ClinVar benchmark preserves ClinVar expected labels but adds
ClinGen-applied ACMG criteria to matched cases.

| Measure | Count |
|---|---:|
| ClinVar cases | 21,638 |
| Direct ClinGen Variation ID matches | 10,649 |
| Canonical-key fallback matches in current fixture | 0 |
| Normalization failures | 2 |
| Unmatched ClinVar cases | 10,989 |
| Cases gaining criteria | 10,649 |
| Total criteria added | 33,094 |
| Cases with enrichment warnings | 11,021 |
| ClinVar/ClinGen label disagreements among matched records | 30 |
| Multiple ClinGen match cases resolved deterministically | 2 |

This reduced serious pathogenic/benign discordances from 34 to 9 and raised
definitive concordance from 5.0% to 37.8%. The enriched fixture still fails the
validation gate because many variants remain unmatched or still lack the full
evidence expert reviewers use.

Canonical-key matching is implemented, but the current ClinGen fixture does not
provide usable loci for its canonical-key index, so current real-data enrichment
still comes from direct Variation ID matches.

## Privacy, Storage, And Reanalysis

The database model separates two domains:

- `clinical`: tenant-scoped, identified clinical data such as patients,
  classifications, sign-off fields, alerts, and reanalysis records.
- `research`: de-identified variant evidence, evidence bundles, source records,
  and cohort counts.

Research tables intentionally carry no patient or tenant identifiers and no foreign
key back to the clinical schema. Tenant isolation and the clinical/research
boundary are covered by tests.

Stored classifications can be verified by replaying the persisted evidence under
the recorded engine/config version and comparing the resulting tier and
reconstruction hash. Evidence-bundle provenance can also be checked for tampering.

Reanalysis can recompute classifications when evidence, provider versions, or
configuration versions change. It avoids duplicate churn, records same-tier changes
as audit events, and creates clinical alerts only on tier crossings.

## Appropriate Use

This proof of concept is appropriate for:

- Auditing how a fixed ACMG/AMP-style rule set behaves on supplied criteria.
- Comparing evidence completeness across public sources.
- Studying which missing evidence categories drive disagreement.
- Reproducing benchmark runs in a controlled local environment.
- Testing provenance-preserving storage, reconstruction, and reanalysis.
- Prototyping a human-reviewed reclassification workflow.

It is not appropriate for:

- Autonomous patient diagnosis.
- Clinical reporting without local validation and sign-off.
- Treatment or management recommendations.
- Estimating penetrance, severity, or personal disease risk.
- Claiming that ClinVar or ClinGen reference labels are ground truth.

## Main Limitations

ReClass re-sums supplied evidence. It does not independently judge the quality of a
paper, functional assay, segregation claim, phenotype match, case-control result,
or expert assertion.

Variant-type coverage is uneven. Automated signals are strongest for missense SNVs.
Splice variants, structural variants, copy-number variants, repeat expansions,
mitochondrial variants, non-coding variants, and complex indels need additional
specialized evidence logic.

Frequency-based reasoning inherits representation limits from gnomAD and related
population resources. Absence from a population database is treated as unknown
evidence unless a configured rule explicitly supports a frequency criterion.

For a fuller boundary statement, read `limitations.md`.

## Unfinished Work

The remaining todo list is in `gap.md`. In short, unfinished work is concentrated
in clinical review, production reference data, broader evidence coverage,
clinician-facing product design, production deployment, source refresh, and local
operating procedures.

## Where To Look Next

- `limitations.md` gives the clinical and scientific boundary statement.
- `research.md` explains how the project relates to published ACMG/AMP tools.
- `plan.md` is the setup and runbook.
- `ReClass Model/README.md` is the technical repository map.
- `gap.md` lists only unfinished todos.
