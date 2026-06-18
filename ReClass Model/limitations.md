# Limitations

This document records the **genuine, structural limits** of the ReClass variant
reclassification engine — the things that bound what this system can ever claim,
given how it is designed and what it actually does.

It is deliberately **not** a todo list. Unfinished work, missing integrations, and
forward plans live in `../gap.md` and `../roadmap.md`. Status of what is built
lives in `README.md` and `manifest.md`. The items below are different in kind:
they are not "not done yet," they are properties of the approach and of the data
that would remain true even if every open task were completed. Where a limit is
proven by the project's own numbers or code, that grounding is cited.

---

## 1. The engine combines evidence; it does not generate it

This is the single most important limitation, and everything else follows from it.

`classify()` is a pure function that sums standardized ACMG/AMP criteria into a
tier. It does not — and by design cannot — discover, derive, or validate the
underlying biology. The scoring module says so directly:

> "The engine does NOT resolve genuinely uncertain biology -- the judgment lives
> in mapping evidence to criteria/strengths (the evidence-integration layer) and
> in the mandatory human sign-off downstream. This module only sums standardized
> evidence into a tier." — [engine/scoring.py](engine/scoring.py)

The evidence layer does not close this gap, because every provider is a **mapper,
not a discoverer**. Each one takes structured evidence that a human, a lab, or an
upstream database has *already produced* and translates it into a criterion code
and strength:

- `PS3`/`BS3` require a functional-assay result already determined to be
  "damaging" or "normal" — the system does not run assays.
- `PM3` requires in-trans observations already collected — it does not phase
  genotypes.
- `PP1`/`BS4` require a segregation/meioses count already worked out from a
  pedigree — it does not interpret families.
- `PP4` requires a phenotype-specificity *label* — it does not measure phenotype
  match.
- `PVS1` requires the caller to assert the gene's loss-of-function mechanism
  (`lof_mechanism`) — it does not establish disease mechanism.

(See [evidence/criteria_ext.py](evidence/criteria_ext.py) and the extended
mappers in [engine/scoring.py](engine/scoring.py).)

The project's own benchmarks quantify this exactly. When the engine is fed the
**complete expert-applied criteria** a ClinGen panel used, it reproduces the
panel's tier ~94.7% of the time (`clingen_real_v1`, 12,446 cases, gate PASS).
When it is fed only the sparse signals that a public source like ClinVar actually
carries — labels plus frequency plus a missense predictor — definitive
concordance collapses to **5.0%** (`clinvar_real_v1`, 21,638 cases, gate FAIL).
Enriching with matched ClinGen criteria lifts that to 42.4%, and no further,
because ~9,668 cases simply have no recoverable structured evidence.

The implication is fundamental: **the system's accuracy is bounded by the
completeness of the evidence handed to it, not by the engine.** The hard,
expensive, judgment-heavy part of variant interpretation — generating functional,
segregation, case-control, de-novo, and mechanism evidence — lives entirely
outside this system and always will.

## 2. "Ground truth" is expert opinion, and the strongest benchmark is partly circular

The validation harness measures concordance against ClinGen VCEP final tiers and
ClinVar curated labels. These are **expert interpretations**, not biological or
clinical-outcome truth. There is no orthogonal gold standard anywhere in the
pipeline: no functional ground truth, no penetrance data, no patient outcomes.

The passing benchmark is closer to a consistency check than a correctness check.
`clingen_real_v1` feeds the engine *the same criteria the panel applied* and then
asks whether the deterministic point-sum reproduces *the same tier the panel
reached*. That tests arithmetic fidelity to the ACMG/AMP point model — it does not
test whether the panel was biologically right. A high ClinGen concordance number
therefore says "the calculator adds up the way the experts did," not "the call is
correct."

Consequently, the validation can demonstrate two things honestly — that the point
arithmetic faithfully implements the framework, and that classification quality is
highly sensitive to evidence completeness — but it **cannot demonstrate clinical
validity**. No benchmark here is an independent, held-out, biologically-anchored
test set, so reported concordance should never be read as a clinical accuracy
figure.

## 3. It inherits every limitation of the ACMG/AMP point framework

The engine is a faithful implementation of the ClinGen SVI Bayesian point system
(Tavtigian et al. 2020): fixed points per evidence strength, additive combination,
and fixed tier cutoffs (see [engine/config.py](engine/config.py) and
[engine/configs/base_v1.json](engine/configs/base_v1.json)). It therefore inherits
the framework's known limits:

- Evidence is forced into a small set of discrete strength tiers
  (supporting/moderate/strong/very-strong), which is an approximation of
  continuous biological signal.
- The additive point model is itself a modeling choice with documented edge cases
  and inter-laboratory discordance; the ACMG/AMP criteria carry well-known
  subjectivity, especially the PP/PM/BP supporting criteria.

Crucially, **being deterministic is not the same as being objective.** Determinism
removes arithmetic and tooling variance; it does not remove judgment. It relocates
*all* of the subjectivity to the upstream step where a human decides which criteria
apply and at what strength. Two competent reviewers who disagree about whether PM2
or PS3_Moderate applies will get two different, equally reproducible answers.

## 4. Computational predictors are missense-only, capped, and contaminated by training overlap

The in-silico evidence the system *can* generate on its own (REVEL, AlphaMissense,
conservation) is narrow by construction:

- **Missense only.** REVEL and AlphaMissense score missense substitutions and
  cover only positions present in their precomputed tables. They contribute
  nothing for loss-of-function, deep-intronic, regulatory, structural, or repeat
  variation.
- **Capped, by design.** Multiple predictors are deliberately collapsed into a
  *single* PP3/BP4 event rather than stacked, and conflicting predictors yield a
  conservative no-call (`resolve_missense_consensus` in
  [engine/scoring.py](engine/scoring.py)). This is correct per ACMG guidance, but
  it means computational evidence can never be more than supporting-to-moderate
  weight on its own.
- **Train/test overlap.** REVEL (v1.3, 2021) and similar predictors were trained
  in part on ClinVar/known-variant labels. Evaluating predictor-driven
  classifications against ClinVar labels therefore **overstates** real-world
  performance on genuinely novel variants. The favorable enrichment numbers should
  be read with this leakage in mind.

## 5. Allele-frequency evidence carries population and ascertainment limits

Frequency-based criteria are derived from fixed gnomAD popmax/FAF cut-points —
BA1 ≥ 5%, BS1 ≥ 1%, PM2 ≤ 0.01% ([engine/config.py](engine/config.py)). Several
limits are intrinsic to this:

- **Ancestry representation is uneven.** gnomAD's populations are not equally
  powered, so a fixed popmax threshold is less reliable for under-represented
  ancestries, and "rare everywhere" is partly an artifact of who was sequenced.
- **Absence is correctly treated as unknown, not rare.** The provider records a
  variant missing from gnomAD as *unknown* frequency, never as AF 0 (see
  [evidence/gnomad.py](evidence/gnomad.py)). This is the right call, but it means a
  genuinely novel pathogenic variant gets **no** frequency-based lift from this
  system.
- **Founder and population-specific variants** need per-variant overrides; a
  global threshold will misjudge them. gnomAD is also explicitly *not* a screened
  healthy-control cohort, so frequency is a proxy, not a disease-status filter.

## 6. Realistic scope is small, single-locus variants on one genome build

Despite the breadth of provider *machinery*, the realistic end-to-end scope is
narrow:

- **GRCh38 only.** There is no liftover guarantee and no GRCh37 path.
- **Indels depend on an external reference that is not part of the system.**
  Reference-free canonical identity works for SNV/MNV; reference-backed
  left-alignment of indels requires a multi-GB GRCh38 FASTA that is deliberately
  not bundled and must be supplied per deployment. On the current real data the
  reference-backed indel match contribution is **0** (see
  [README.md](README.md) and [evidence/enrich_clinvar.py](evidence/enrich_clinvar.py)).
- **CNV, structural, repeat-expansion, mitochondrial, and non-coding "providers"
  only map supplied categories.** They do not call variants, interpret raw signal,
  or measure dosage/heteroplasmy/repeat counts — those must arrive pre-determined
  ([evidence/criteria_ext.py](evidence/criteria_ext.py)). So for everything beyond
  small coding variants, the system is a scorer of someone else's structured
  findings, not an analyzer of biology.

## 7. It classifies variants, not patients

The output is a variant-level ACMG/AMP tier. It is not a clinical diagnosis and
does not model the patient:

- No integration of the individual's phenotype beyond a supplied PP4 specificity
  label; no observed zygosity, family history, or de-novo status except as
  pre-encoded criteria.
- No penetrance, expressivity, age-of-onset, or actionability modeling.
- No gene–disease validity assessment and no mode-of-inheritance reasoning.

A "Pathogenic" tier is an assertion about a variant's relationship to a condition
under the ACMG framework. It is **not** a statement that the variant causes disease
in a specific person, nor a measure of risk or clinical management. That
translation remains a clinician's job — which is why the system is designed as
decision support requiring credentialed human sign-off, never as an autonomous
diagnostic.

## 8. Gene- and disease-specific calibration is, and will remain, largely generic

ACMG/AMP criteria are known to require gene- and disease-specific tuning, which is
why ClinGen publishes VCEP-specific specifications (CSpecs). This system encodes
that specificity for only a **handful** of cases — Hearing Loss proband-count PS4
for `COCH`, `KCNQ4`, and `MYO6`, and a denominator-aware Cardiomyopathy
odds-ratio/CI path — with a conservative generic fallback everywhere else
([monitoring/reanalysis.py](monitoring/reanalysis.py),
[docs/data_governance.md](docs/data_governance.md)).

This is a structural limit, not merely an unfinished feature: comprehensive
per-gene calibration is a perpetually-moving, expert-curation effort across
hundreds of gene–disease pairs whose specs are revised over time. Any deployment
will, for the overwhelming majority of genes, be applying **generic thresholds to
genes that have their own published specifications** — which is inherently less
accurate than a VCEP would be for those genes, and cannot be "finished" the way a
software feature can.

## 9. Every classification is a snapshot of moving evidence

Variant interpretation is not stationary; evidence accrues continuously and
classifications drift. This system's outputs are only as current as the database
snapshots behind them — ClinVar (a dated weekly release), the ClinGen ERepo export,
gnomAD v4.1, REVEL v1.3 (2021). The reanalysis and tier-crossing machinery can
*re-run* scoring when inputs change, but it cannot manufacture new biological
evidence; it only detects that the inputs moved.

The reconstruction hash guarantees you can reproduce a **past** classification
byte-for-byte; it does not guarantee that classification is **still correct
today**. Reproducibility and currency are different properties, and the system
provides only the former.

## 10. Reproducibility guarantees auditability, not correctness

The engine's defining strength — a pure, deterministic core with a SHA-256
reconstruction hash — is a guarantee about *process*, not *truth*. Given the same
evidence and the same engine/config version, you always get the same tier and the
same hash. That makes any result auditable and re-derivable.

It does nothing to ensure the result is right. Incorrect or incomplete supplied
criteria produce a wrong tier that is, nonetheless, perfectly reproducible and
fully attributable. "Garbage in, reproducible garbage out" is the honest framing:
the system can prove *how* it reached a classification, never that the
classification reflects the patient's biology.

---

### Summary

ReClass is a faithful, auditable, deterministic implementation of the ACMG/AMP
point framework. Its limits are not bugs and mostly not gaps — they are the
boundary of what an evidence-*combining* engine, validated against
expert-*opinion* labels, scored under a *discrete* framework, on *snapshot* data,
for *variants rather than patients*, can ever claim. Used as decision support with
credentialed human sign-off and complete upstream evidence, it is sound. Asked to
*replace* the evidence-generation and clinical judgment it depends on, it cannot,
and no amount of additional engineering within this design would change that.
