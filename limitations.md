# Limitations - Genuine Model Boundaries

This file describes the genuine limits of application and realism for the
Standardized Variant Reclassification Engine in `ReClass Model/`.

It is intentionally not a roadmap. It does not list features to build, bugs to fix,
or engineering next steps. Those belong in `gap.md`. The purpose of this document
is to state what the model cannot legitimately claim, even when the code is working
as intended.

The shortest honest description is:

> ReClass is a deterministic calculator for structured ACMG/AMP-style evidence. It
> is not a clinical authority, not an evidence-discovery system, not a biological
> truth oracle, and not a general model of patient risk.

---

## What the validation actually shows

The validation results establish a useful boundary around the project:

| Benchmark | Cases | Evidence condition | Definitive concordance | Serious discordance | What it means |
|---|---:|---|---:|---:|---|
| `synthetic_v1` | 25 | Hand-authored rule cases | 90.5% | 0 | Harness and scoring plumbing behave as expected; this is not clinical evidence. |
| `clingen_real_v1` | 12,446 | Expert-applied ClinGen criteria are supplied | 94.7% | 4 | The point model usually reproduces panel tiers when fed structured expert criteria. |
| `clinvar_real_v1` | 21,638 | Sparse public signals: mostly labels plus REVEL/frequency | 5.0% | 34 | Sparse public annotations do not reproduce expert classification. |
| `clinvar_enriched_v1` | 21,638 | ClinVar plus direct ClinGen evidence matches where available | 37.8% | 9 | Adding expert criteria helps substantially, but coverage remains the limiting factor. |

These numbers do not say "the model is clinically accurate." They say something
narrower and more important: the same deterministic scoring logic works well when
the right structured evidence is supplied and fails honestly when that evidence is
absent. The realism limit is therefore not just code quality. It is the availability,
quality, specificity, and human interpretation of evidence.

## 1. It is decision support, never a diagnosis

ReClass produces an evidence-based tier calculation. It does not produce a medical
diagnosis, a clinical reportable result, or an autonomous treatment recommendation.

This is a hard boundary of the project:

- It cannot be used without qualified human review.
- It cannot decide whether a patient has a disease.
- It cannot decide whether a variant should be reported clinically.
- It is not FDA-cleared, CLIA-validated, or otherwise authorized as a clinical
  device.

Human sign-off is not a user-interface detail. It is part of the valid scope of the
model.

## 2. It scores evidence; it does not create evidence

The engine can combine structured criteria that are supplied to it. It cannot
independently determine whether those criteria are true.

For example, it cannot:

- read a paper and decide whether the study supports PS3 or BS3,
- judge whether a functional assay is valid for a disease mechanism,
- assess segregation evidence from a pedigree,
- determine phenotype specificity from a patient chart,
- verify whether a ClinVar or ClinGen assertion is correct,
- discover missing case-control, proband, or family evidence.

This is the central model limitation. ReClass is a calculator over evidence, not an
expert curator. If the evidence is absent, incomplete, incorrectly mapped, outdated,
or biologically inappropriate, the calculation inherits that problem.

## 3. A VUS can be a real endpoint, not a failure state

Many variants are genuinely uncertain because the world does not yet contain enough
reliable evidence about them. In those cases, "VUS" is not a placeholder for a
future feature. It is the correct epistemic state.

The model cannot make a rare or poorly studied variant meaningful by force. It can
only expose that available evidence does not justify a pathogenic or benign tier.
This matters because pressure to avoid VUS can create false certainty.

## 4. The point model is a simplified model of expert reasoning

ReClass uses a deterministic Tavtigian/ClinGen SVI-style point system. That choice
gives reproducibility, auditability, and reconstruction hashes, but it also creates
a realism ceiling.

Expert ACMG/AMP interpretation is not purely additive. Human panels consider
context such as:

- gene-specific disease mechanism,
- disease prevalence and penetrance,
- known founder effects,
- assay validity,
- variant mechanism,
- inheritance mode,
- phenotype fit,
- criterion interactions,
- panel-specific rule modifications,
- whether conflicting evidence should be discounted rather than summed.

A transparent point total can approximate this reasoning. It cannot fully become
it. The residual serious discordances in `clingen_real_v1`, despite supplied expert
criteria, are evidence of that ceiling.

## 5. Agreement with references is not the same as truth

The validation reports measure concordance with reference labels. Those labels are
important, but they are not biological ground truth.

ClinVar and ClinGen assertions can be:

- incomplete,
- outdated,
- inconsistent across submitters or panels,
- affected by changing guideline versions,
- affected by panel-specific disease context,
- revised when new evidence appears.

Therefore a high concordance number means "the model reproduces this reference
source under these inputs." It does not prove that the variant tier is true in a
timeless or absolute sense.

## 6. The benchmarks are not equivalent to a clinical intake stream

The fixtures are useful for reproducible validation, but they are not a faithful
sample of future clinical work.

Public databases over-represent variants that have already attracted expert
attention. A real intake stream can contain novel, private, rare, poorly
phenotyped, or technically difficult variants. Those are exactly the cases where
structured evidence is least available and where deterministic scoring has the
least to work with.

The synthetic benchmark is especially limited: it checks the harness and rule
plumbing. It should not be cited as clinical performance evidence.

## 7. Automated evidence coverage is biologically narrow

The model's strongest automated evidence channels are currently structured
ClinGen-applied criteria, REVEL missense prediction, gnomAD frequency evidence, and
configured cohort-count PS4 evidence.

That makes the automated layer much more realistic for some situations than others.
It is weakest where classification depends on evidence types the system cannot
derive on its own, including:

- splice mechanism,
- copy-number variation,
- structural variation,
- repeat expansion,
- mitochondrial interpretation,
- non-coding regulatory impact,
- complex indels,
- functional assay interpretation,
- detailed phenotype matching,
- segregation analysis.

This is not just an implementation gap. Many of these evidence classes require
human domain judgment, disease-specific standards, laboratory context, or external
data that is not reducible to a universal annotation score.

## 8. Population-frequency reasoning inherits population bias

Frequency criteria such as BA1, BS1, and PM2 depend on reference population data.
Those data are uneven across ancestries and cohorts. If a population is
under-sampled, absence or rarity in gnomAD is weaker evidence than it might appear.
If a founder pathogenic variant is common in a particular group, naive frequency
rules can be actively misleading.

The engine can record thresholds and provenance, but it cannot remove ascertainment
bias from the source database. It also cannot make equity claims from the current
real fixtures, because the available real-data group fields primarily encode
expert-panel context rather than true patient ancestry strata.

## 9. The model is not a patient-risk model

An ACMG/AMP tier is about evidence for variant pathogenicity. It is not the same as
an individual's medical prognosis.

ReClass does not estimate:

- penetrance,
- expressivity,
- age of onset,
- disease severity,
- treatment response,
- management recommendations,
- personal absolute risk,
- reproductive risk outside the supplied inheritance context.

Using a tier as though it directly answered any of those questions exceeds the
model.

## 10. Determinism is version-bounded

The reproducibility guarantee is precise but limited:

> same evidence + same engine version + same configuration version = same result
> and same reconstruction hash.

It does not mean the classification is permanent. Updated evidence, updated source
snapshots, updated VCEP guidance, or changed configuration can correctly produce a
different tier. A result is a versioned snapshot of evidence interpretation, not a
timeless property of the variant.

## 11. The model does not learn

ReClass is deliberately a rules engine. It does not update itself from outcomes,
discover new pathogenic mechanisms, infer new evidence types, or recalibrate
thresholds by watching future cases.

That limitation is also part of its strength. The model is inspectable and
reconstructable because the rules are explicit. The cost is that biological
novelty, guideline change, and evidence reinterpretation must enter through
reviewed evidence and reviewed configuration, not through automatic adaptation.

## Bottom line

ReClass is most realistic when it is used as a transparent, reproducible calculator
for already-curated ACMG/AMP evidence. It is least realistic when it is asked to act
like an autonomous curator, clinician, literature reviewer, phenotype interpreter,
or all-variant biological predictor.

Its genuine limits are that it cannot manufacture evidence, cannot exceed the
quality of its sources, cannot convert concordance into truth, cannot replace
expert judgment, cannot make equity claims from biased reference data, cannot
generalize equally across variant classes, and cannot be used as a clinical device.
