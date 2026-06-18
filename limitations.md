# Limitations - Genuine Model Boundaries

This file states the real limits of the model in
[`ReClass Model/`](ReClass%20Model/). It is not a backlog, a bug list, a release
plan, or a set of improvement suggestions. Those live in [`gap.md`](gap.md) and
[`roadmap.md`](roadmap.md).

The purpose here is narrower: to say what ReClass can and cannot legitimately be
used to claim, even when the implementation is working as intended.

The shortest honest version:

> ReClass is a deterministic calculator for structured ACMG/AMP-style variant
> evidence. It is not a clinical authority, not an autonomous evidence curator,
> not a biological truth oracle, and not a patient-risk model.

## What Is Being Modeled

ReClass models the final scoring step of germline variant interpretation: given a
set of structured evidence events, it applies a versioned ACMG/AMP-style point
configuration and returns a five-tier classification:

- Pathogenic
- Likely Pathogenic
- Variant of Uncertain Significance
- Likely Benign
- Benign

That is the model boundary. ReClass does not model the entire clinical
interpretation process. In real practice, variant classification also depends on
literature review, assay validation, disease mechanism, inheritance context,
phenotype fit, segregation review, cohort interpretation, ancestry-aware frequency
judgment, VCEP-specific rules, laboratory policy, and credentialed sign-off. The
folder contains infrastructure around some of those activities, but the scoring
model itself remains a structured-evidence calculator.

## What The Validation Actually Shows

The validation fixtures bound a specific question: how the same deterministic
scoring engine behaves under different evidence conditions. They do not prove
clinical readiness or biological truth.

| Benchmark | Cases | Evidence condition | Definitive concordance | Serious discordance | What it shows |
|---|---:|---|---:|---:|---|
| `synthetic_v1` | 32 | Hand-authored rule cases | 92.9% | 0 | The harness and scoring plumbing behave as expected on curated examples |
| `clingen_real_v1` | 12,446 | Expert-applied ClinGen criteria supplied | 94.7% | 4 | The point model usually reproduces expert-panel tiers when supplied expert criteria |
| `clinvar_real_v1` | 21,638 | Sparse public labels plus limited REVEL/frequency signals | 5.0% | 34 | Sparse public annotations do not reproduce expert classification |
| `clinvar_enriched_v1` | 21,638 | ClinVar plus matched ClinGen criteria where available | 42.4% | 6 | Recovered structured criteria help substantially, but evidence coverage still limits realism |

The central finding is not "the model is clinically accurate." It is that the
scoring core is most realistic when the relevant curated evidence has already
been supplied, and least realistic when asked to infer a full classification from
sparse public signals.

The validation therefore bounds evidence dependence, not autonomous clinical
competence.

## 1. ReClass Is Decision Support, Not A Diagnosis

ReClass cannot diagnose a patient, decide whether a patient has a disease, decide
what should be reported clinically, recommend treatment, or substitute for a
qualified reviewer. Its output is an evidence-based tier and an auditable receipt,
not a medical conclusion.

The folder includes draft persistence, reviewer reports, patient-safe summaries,
sign-off states, and API surfaces, but those do not turn the model into a
clinical device. The current configuration state explicitly remains
`governance_reviewed_pending_credentialed_signoff`, and the project is not
FDA-cleared, CLIA-validated, or authorized for autonomous patient reporting.

## 2. The Model Scores Evidence; It Does Not Establish Evidence

The engine can combine ACMG/AMP criteria and selected source signals. It cannot
independently determine whether the evidence itself is true.

It cannot, by itself:

- read the literature and decide whether a paper supports PS3, BS3, PS4, PP1, or
  PP4;
- decide whether a functional assay is valid for a specific gene, disease, and
  mechanism;
- assess segregation from a raw pedigree;
- determine phenotype specificity from a patient chart;
- decide whether a ClinVar or ClinGen assertion is currently correct;
- infer missing case-control, proband, phasing, or family evidence that was never
  supplied;
- resolve source conflicts without human review and local policy.

The extended providers in `ReClass Model/evidence/criteria_ext.py` do not remove
this limit. They map structured inputs into criteria. They do not turn raw
biology, raw charts, raw papers, or raw pedigrees into validated evidence on their
own.

## 3. Evidence Completeness Is A Hard Realism Limit

The model can only be as realistic as the evidence bundle it receives. When the
right structured evidence is supplied, the ClinGen benchmark shows high
concordance with reference panel tiers. When evidence is sparse, the raw ClinVar
benchmark collapses.

That is not just an implementation gap. Variant interpretation often depends on
evidence classes that are not universally available, not machine-readable, not
current, not public, or not reducible to a single annotation score. More source
coverage can improve a particular evidence class, but the model will still
inherit the quality, specificity, and provenance of the evidence it is given.

Absence of evidence in ReClass must not be read as evidence of benignity,
pathogenicity, or clinical irrelevance.

## 4. A VUS Can Be The Correct Final Output

ReClass is allowed to return Variant of Uncertain Significance because many
variants are genuinely uncertain. A VUS is not automatically a failure, a bug, or
an unfinished classification.

Under the configured point model, a VUS means the supplied evidence does not reach
the pathogenic or benign threshold. For rare, novel, private, poorly studied, or
context-dependent variants, that may be the most accurate answer available. Using
the model to force resolution would create false certainty rather than better
classification.

## 5. The Point System Is A Simplification Of Expert Reasoning

ReClass uses a transparent Tavtigian/ClinGen SVI-style Bayesian point framework:
evidence strengths become signed points, and point totals map to classification
tiers. This gives reproducibility, auditability, and stable reconstruction hashes.

It also imposes a ceiling. Expert interpretation is not purely additive. Human
panels consider disease mechanism, inheritance, penetrance, phenotype fit,
population structure, assay validity, source confidence, criterion interactions,
and VCEP-specific rule modifications. Sometimes evidence should be discounted,
excluded, or treated as context-specific rather than simply summed.

The four serious discordances in `clingen_real_v1` are evidence of that ceiling:
even with expert-applied criteria already supplied, a general point model does not
perfectly reproduce panel decisions.

Encoding VCEP, gene, or disease overrides can reduce this mismatch for a defined
scope, but it does not remove the limit. Disease-specific interpretation is a
moving expert-curation activity, not something the general point model can infer
from coordinates alone.

## 6. Reference Concordance Is Not Biological Truth

The validation numbers measure agreement with reference labels, not truth in an
absolute sense. ClinVar and ClinGen records can be incomplete, outdated,
panel-specific, disease-context-specific, or revised when evidence changes.

So "94.7% definitive concordance" means the engine usually reproduces the
reference panel tier under that fixture and evidence condition. It does not prove
that every reproduced tier is biologically correct, clinically reportable, or
permanent.

Likewise, poor raw ClinVar concordance does not prove the scoring logic is
broken. It proves that sparse public labels and a small set of automated signals
are not enough to reconstruct the evidence experts used.

## 7. The Benchmarks Are Not A Real Clinical Intake Stream

The current real-data fixtures are useful for reproducible evaluation, but they
are not a representative sample of future clinical workload.

Public databases over-represent variants that have already attracted attention,
curation, publication, or submission. A real intake stream may contain private
variants, novel alleles, low-quality external evidence, missing phenotype data,
technically difficult variant classes, and cases where laboratory context matters.

The synthetic fixture is especially limited: it exercises rules and plumbing. It
should not be cited as clinical performance evidence.

## 8. Variant-Class Coverage Is Uneven By Nature

ReClass is strongest where evidence can be expressed cleanly as ACMG/AMP criteria
or calibrated source signals: expert-applied ClinGen criteria, allele frequency,
missense computational evidence, and reviewer-supplied structured criteria.

It is less realistic for variant classes and contexts where interpretation
depends on highly specialized, disease-specific, or patient-specific judgment,
including:

- structural variants and complex rearrangements;
- repeat expansions;
- mitochondrial variants and heteroplasmy;
- non-coding and regulatory variants;
- complex indels;
- mosaicism;
- pharmacogenomic or risk-modifier interpretations;
- somatic oncology interpretation;
- polygenic risk or multifactorial disease risk.

Some of these have structured-input providers in the folder. That means the
system can score a reviewed signal if one is supplied. It does not mean the model
autonomously understands every biological context behind that signal.

## 9. Automated Source Signals Are Narrow Proxies

The source signals ReClass can partly automate are useful, but each is a proxy for
a narrower evidence question:

- REVEL and AlphaMissense apply to missense substitutions covered by their
  precomputed tables. They do not help with loss-of-function, regulatory,
  structural, repeat-expansion, mitochondrial, or many splice mechanisms.
- Computational predictors are intentionally collapsed into a single PP3/BP4-style
  contribution rather than stacked as independent evidence. They can support a
  classification; they cannot carry one on their own.
- Public-label and predictor benchmarks can be inflated by source overlap, because
  predictors and public assertions may share historical ClinVar or known-variant
  training material.
- ClinGen evidence matching transfers criteria that expert panels already applied.
  It does not create criteria for unmatched variants or prove that a matched
  assertion remains correct in a different clinical context.

These are not bugs. They are the realism boundary of automated evidence signals:
they are evidence fragments, not comprehensive variant interpretation.

## 10. Population-Frequency Reasoning Inherits Population Bias

BA1, BS1, and PM2-style frequency evidence depends on reference-population data.
Those data are uneven across ancestries, cohorts, sequencing methods, and disease
ascertainment histories.

If a population is under-sampled, rarity in a database may be less meaningful
than it appears. If a founder pathogenic allele is relatively common in a
specific group, a naive frequency threshold can be misleading. ReClass can record
thresholds, source versions, warnings, and reviewable exceptions, but it cannot
remove bias from the underlying population resource.

Current fixtures also do not support strong equity or ancestry-performance
claims. The relevant patient-ancestry data are largely absent, so the model cannot
be said to have been validated equally across populations.

## 11. A Variant Tier Is Not A Patient-Risk Estimate

An ACMG/AMP tier is a statement about evidence for variant pathogenicity in a
specified interpretive context. It is not an individualized clinical-risk model.

A ReClass tier must not be interpreted as:

- penetrance;
- expressivity;
- age of onset;
- prognosis;
- disease severity;
- treatment response;
- reproductive risk without a supplied inheritance context;
- patient-management guidance.

Those conclusions require clinical context outside the model.

## 12. Determinism Is Version-Bounded

ReClass is deterministic in a precise sense:

> same evidence + same engine version + same configuration version = same tier
> and same reconstruction hash.

That does not mean a classification is permanent. A tier can correctly change
when evidence changes, source snapshots change, VCEP guidance changes, or a
versioned configuration changes. Reanalysis detects and records that movement; it
does not eliminate it.

The output is a reproducible snapshot of an evidence interpretation, not a
timeless property of the variant.

## 13. The Model Does Not Learn Or Discover Biology

The scoring core is deliberately rule-based. It does not train on outcomes,
update itself from future cases, infer new mechanisms, recalibrate thresholds in
the background, or discover new disease relationships.

That is a strength for auditability and reconstruction. It is also a limit:
biological novelty, guideline change, and source reinterpretation enter only
through reviewed evidence and reviewed configuration.

## 14. Local Workflow Surfaces Do Not Expand The Model Boundary

The folder contains API routes, storage, reporting, audit logging, authentication
surfaces, reviewer UI, reanalysis queues, FHIR serialization, and deployment
artifacts. These are workflow and governance infrastructure around the model.

They do not change what the model can infer. A polished service surface can make
classification review more traceable, but it cannot turn sparse evidence into
complete evidence, turn concordance into truth, or turn decision support into
clinical authority.

## Bottom Line

ReClass is most realistic when used as a transparent, reproducible calculator for
already-curated ACMG/AMP-style evidence. It is least realistic when asked to act
as an autonomous curator, clinician, literature reviewer, phenotype interpreter,
or universal biological predictor.

Its genuine limits are that it cannot manufacture evidence, cannot exceed the
quality and completeness of its sources, cannot convert benchmark concordance into
truth, cannot fully reproduce expert judgment with an additive point total,
cannot turn narrow automated signals into comprehensive interpretation, cannot
make patient-risk or management claims, cannot erase population bias, and cannot
be used as a clinical device without external validation, governance, and
credentialed human review.
