# Proposal — Hypothetical Changes to the ACMG/AMP Scoring Criteria

Author: project review pass
Date: 2026-06-17
Status: **exploratory research discussion — not an adopted standard, not a software change**

## What this document is (and is not)

This is **not** a proposal to change the ReClass engine. The engine faithfully
implements the published standard — the ACMG/AMP 2015 framework (Richards et al.),
the Tavtigian et al. 2020 point translation, and the ClinGen SVI refinements — and
it should keep doing so. It is used here only as an *instrument*: because it
reproduces the standard deterministically across ~34,000 real variants, it surfaces
the places where the **standard's own scoring rules**, applied exactly as written,
diverge from expert-panel intent.

This document proposes **hypothetical changes to the standard's scoring criteria**,
each stated as: *current rule → hypothetical change → reasoning → trade-off*. These
are research positions for discussion, deliberately consistent with the project's
stance that it does **not** itself assert new ACMG/AMP combining rules
([`research.md`](research.md) §1, §5). The point of grounding them in ReClass data
is that the divergences are *measured*, not hypothesized.

The evidence base is the four validation fixtures and their reports
([`validation/reports/`](ReClass%20Model/validation/reports/)), which show the
identical engine reaching 94.7% definitive concordance on expert-supplied ClinGen
criteria but mis-calling a small, **systematic** set of cases. Every serious
pathogenic↔benign error traces to a rule *in the standard*, not to arithmetic
(the threshold-sensitivity sweeps show the point values are already near-optimal:
moving the pathogenic cutoffs ±2 only lowers concordance —
[`calibration_clingen_real_v1.md`](ReClass%20Model/validation/reports/calibration_clingen_real_v1.md)).

---

## Summary of proposed criterion changes

| # | Change to the standard | Rule affected | Motivating ReClass evidence |
|---|---|---|---|
| **C1** | Make `BA1` a **conditional** stand-alone benign rule, not an unconditional override | ACMG/AMP 2015 stand-alone benign | GJB2/SLC26A4 founder variants mis-called Benign |
| **C2** | Forbid a **Likely** (or stronger) call from computational evidence alone | PP3/BP4 (in-silico) | BRCA1 variants called Likely Benign on REVEL alone |
| **C3** | Restore the 2015 **"conflicting evidence → Uncertain"** rule that the point translation dropped | Tavtigian point netting | HNF4A: strong benign netted against pathogenic |
| **C4** | Require **filtering allele frequency (FAF95) + a minimum allele-number gate** for BA1/BS1/PM2 | Frequency criteria | Founder/under-sampled-population over-calls |
| **C5** | Re-examine the **benign-side threshold asymmetry** (a single supporting benign reaches Likely Benign) | Tavtigian tier cutoffs | Benign over-calling in sparse evidence |
| **C6** | Specify a **calibrated combined strength** for concordant in-silico predictors (not the maximum) | SVI "single PP3/BP4" rule | Latent PP3 over-call from correlated predictors |

---

## C1 — Make BA1 a *conditional* stand-alone benign rule

**Current standard.** ACMG/AMP 2015 designates BA1 (allele frequency above a benign
threshold, default ≥5%) as the one **stand-alone benign** criterion: it classifies a
variant as Benign by itself, overriding all other evidence. In point terms this is an
absolute short-circuit, not an additive contribution.

**Hypothetical change.** BA1 should be stand-alone **only when no Strong-or-greater
pathogenic criterion (PVS1 or any PS) is also met.** When BA1 co-occurs with such
evidence, the standard should default the variant to **"Conflicting / Uncertain —
adjudication required,"** resolvable to Benign only through a documented, curated
exception (the same shape ClinGen SVI already uses for its BA1 exception list — this
would generalize that list into a structural rule).

**Reasoning.** Founder and high-carrier pathogenic alleles are genuinely *common in
specific populations yet pathogenic*, which is exactly the case the unconditional
override mishandles. Reproducing the standard verbatim, ReClass mis-calls all three
such variants as Benign despite strong curated pathogenic evidence
([`calibration_clinvar_enriched_v1.md`](ReClass%20Model/validation/reports/calibration_clinvar_enriched_v1.md)):

- **GJB2 c.35delG** — PVS1 + PS4 + PM3 (net **+6** after BA1) → forced **Benign**.
- **GJB2 c.167delT** — PVS1 + PM3 (net **+2**) → forced **Benign**.
- **SLC26A4 c.349C>T** — PP1(strong) + PM3 + PP3 + PP4 (net **+3**) → forced **Benign**.

These are the *dominant* serious-error class in the benchmark. A frequency rule that
can silently veto very-strong functional/segregation evidence is a defect *in the
criterion's design*, not in any implementation of it.

**Trade-off.** Introduces a conflicting/uncertain state that requires human
adjudication, and depends on accurate population frequency (see C4). It slightly
raises the uncertain rate in exchange for eliminating false-benign calls on
clinically important founder variants.

---

## C2 — Forbid a "Likely" or stronger call from computational evidence alone

**Current standard.** Computational predictors map to PP3 (pathogenic) / BP4
(benign). Under SVI calibration (Pejaver et al. 2022) these can reach up to *strong*.
In the point translation, a single benign predictor at moderate strength is −2
points, which already lands in the Likely Benign band (which begins at −1).

**Hypothetical change.** Add an explicit floor to the standard: a classification of
**Likely Benign / Likely Pathogenic or stronger requires at least one
non-computational line of evidence.** In point terms, computational-only scores cap
at Uncertain in either direction.

**Reasoning.** The ACMG/AMP 2015 text already treats in-silico evidence as
*supporting* and warns against over-reliance, but the point arithmetic lets a lone
predictor cross a definitive-direction boundary. Reproducing the standard, ReClass
makes three serious errors that are *only* a single REVEL score
([`calibration_clinvar_enriched_v1.md`](ReClass%20Model/validation/reports/calibration_clinvar_enriched_v1.md)):

- **BRCA1 CV-55432 / CV-54758 / CV-266331** — only signal is REVEL 0.061–0.169 →
  BP4 (moderate, −2) → **Likely Benign**, for variants the ENIGMA panel calls
  Pathogenic.

Predictors also share training data with ClinVar, so a predictor-only "benign" is
partly circular rather than independent confirmation
([`limitations.md`](limitations.md) §9). Making the floor explicit in the *criterion
rules* (rather than leaving it to each implementation's discretion) closes a gap the
guideline only addresses in prose.

**Trade-off.** Converts some confident calls into Uncertain where evidence is thin —
which is the honest answer, but raises VUS volume.

---

## C3 — Restore the 2015 "conflicting evidence → Uncertain" rule lost in the point translation

**Current standard.** ACMG/AMP 2015 states qualitatively that when a variant meets
criteria for **both** pathogenic and benign and the lines of evidence conflict, it
should default to Uncertain Significance. The Tavtigian 2020 point system, however,
simply **nets** opposing evidence into a single signed total — the explicit
conflict rule does not survive the translation.

**Hypothetical change.** Reinstate the conflict rule inside the point framework:
when **Strong-or-greater evidence exists in both directions** (≥1 PVS1/PS pathogenic
*and* ≥1 BA1/BS benign), classify as **Conflicting / Uncertain** rather than
returning the netted definitive tier.

**Reasoning.** Opposing strong evidence usually signals an *unresolved*
interpretation, not a confident average. Reproducing the netting rule, ReClass
mis-calls HNF4A **CG-9212** (Likely Pathogenic per the Monogenic Diabetes VCEP):
strong benign (BS1 + BS2) plus BP5 net against PP1 + PP3 + PP4 to **−3 → Likely
Benign** ([`calibration_clingen_real_v1.md`](ReClass%20Model/validation/reports/calibration_clingen_real_v1.md)).
A purely additive total hid a real evidence conflict behind a single number; the
2015 guideline would not have called this benign.

**Trade-off.** More variants land in Uncertain. This is a feature where the conflict
is genuine, but it must be scoped (e.g., only strong-vs-strong) so ordinary
supporting-level disagreement still nets normally.

---

## C4 — Require filtering allele frequency (FAF95) with an allele-number gate

**Current standard.** ACMG/AMP 2015 frames BA1/BS1/PM2 around population allele
frequency thresholds and predates routine availability of filtering allele
frequencies. The original criterion does not mandate the *lower bound of the 95% CI*
(FAF95) nor a minimum sample size, so a point-estimate popmax frequency can trigger
a benign criterion even when computed from very few alleles.

**Hypothetical change.** The frequency criteria should be redefined to require:
(a) the **FAF95 popmax** rather than a raw point estimate, and (b) a **minimum
allele-number / confidence gate** below which BA1/BS1 may **not** be applied
(absence or low confidence is not evidence of commonness).

**Reasoning.** Point-estimate frequencies over-call benignity in under-sampled
populations and around founder effects — the same fragility behind C1's founder
errors, and a direct equity concern: rarity in a poorly sampled population is less
meaningful than it appears ([`limitations.md`](limitations.md) §10). gnomAD v4.1
already publishes FAF and exome/genome discordance flags
([`research.md`](research.md) §2.2), so this is best practice the *criterion text*
has simply not caught up to. This is arguably the most defensible change because it
tightens rather than loosens, and the better statistic already exists.

**Trade-off.** Slightly fewer benign calls overall; requires sources to expose
FAF95 and allele-number fields (which gnomAD does).

---

## C5 — Re-examine the benign-side threshold asymmetry

**Current standard.** Tavtigian 2020 tier cutoffs place Likely Benign at **−1 to −6**
but Likely Pathogenic at **+6 to +9**. So a *single* supporting benign criterion
(−1) reaches Likely Benign, whereas a single supporting pathogenic criterion (+1)
stays Uncertain. Benign classifications are "cheaper" than pathogenic ones.

**Hypothetical change.** Consider raising the Likely Benign threshold (e.g., to −2,
or requiring ≥2 benign criteria) so a lone supporting benign signal remains
Uncertain — mirroring the conservatism already applied on the pathogenic side.

**Reasoning.** The asymmetry is a deliberate Bayesian artifact (a random variant is
*a priori* more likely benign), and that justification is sound for estimating a
posterior probability. But in a **clinical-reporting** context the loss function is
asymmetric in the other direction: a false "Likely Benign" (a missed pathogenic
variant) is more harmful than a false "Uncertain." ReClass over-calls toward benign
precisely in the sparse-evidence regime where a single weak signal dominates
([`calibration_clinvar_enriched_v1.md`](ReClass%20Model/validation/reports/calibration_clinvar_enriched_v1.md)
shows large Likely-Benign-direction movement on thin evidence). This is the most
*debatable* proposal here and is offered explicitly as a question: should the
standard optimize a posterior probability or a clinical loss function?

**Trade-off.** Would increase the Uncertain rate and partially undo the statistical
calibration that makes the point system Bayesian-consistent. Include only with
explicit acknowledgement of that tension.

---

## C6 — Specify a calibrated combined strength for concordant in-silico predictors

**Current standard.** ClinGen SVI recommends that multiple in-silico missense
predictors contribute a **single** PP3/BP4 (no stacking). It does not precisely
specify the *strength* of that single criterion when two calibrated predictors
agree.

**Hypothetical change.** Define the combined strength as the **calibrated strength of
the joint approach** (or, absent a joint calibration, the *more conservative* of the
two predictors) — explicitly **not** the maximum of the individual strengths.

**Reasoning.** Predictors share input features and training data, so concordance is
correlated, not independent confirmation; taking the stronger of two correlated
calls biases PP3 upward. ReClass's reproduction of "take the stronger on agreement"
([`engine/scoring.py:851-863`](ReClass%20Model/engine/scoring.py#L851-L863)) is a
reasonable reading of an *under-specified* rule — which is the point: the standard
leaves a gap that different tools fill differently, hurting cross-lab
reproducibility. No current serious error traces to this, so it is low priority, but
it is a real ambiguity worth closing in the criterion text.

**Trade-off.** Marginally weaker computational pathogenic pull; needs a published
joint calibration to be fully principled.

---

## How these would be evaluated (without changing the engine)

Because the engine is configuration-driven and versioned, each hypothetical criterion
change can be **simulated** as a new scoring configuration and measured against the
same fixtures, then discarded — the production engine is untouched:

1. Express the change as a candidate config / rule variant and re-run calibration on
   all four fixtures ([`validation/calibration.py`](ReClass%20Model/validation/calibration.py)).
2. Primary readout: does the **serious pathogenic↔benign error count fall** (C1, C2,
   C3 target zero) **without regressing** the 94.7% clingen-real definitive
   concordance? That guardrail distinguishes a genuine criterion improvement from a
   trade that just moves errors around.
3. Quantify the cost of each change — how many currently-correct calls move into
   Uncertain — via the before/after confusion-matrix deltas the comparison reports
   already produce ([`compare_reports.py`](ReClass%20Model/validation/compare_reports.py)).

This keeps the proposals *empirical and reversible*: they are claims about the
standard, tested with the standard's own deterministic re-implementation, with no
commitment to alter either the criteria or the model until a credentialed panel
adopts them.

---

## Boundaries

- These are **hypothetical revisions to a clinical standard**, not adopted rules and
  not software changes. Any real change to the ACMG/AMP criteria is the province of
  ACMG, AMP, and ClinGen expert panels, not this project.
- ReClass's role is evidentiary: it shows, on real data, *where* and *how often* the
  current criteria produce expert-discordant calls. It does not have the authority to
  redefine the criteria, and it should continue to implement whatever standard is in
  force, faithfully and reconstructably ([`limitations.md`](limitations.md) §5–6).
</content>
