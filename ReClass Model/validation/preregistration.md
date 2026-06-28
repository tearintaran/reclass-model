# ReClass Held-Out Validation — Pre-Registration v1

> Status: **registered** · 2026-06-23
> Machine-readable contract: [`preregistration.json`](preregistration.json)
> Enforced by: [`holdout_eval.py`](holdout_eval.py), [`fixture_splits.py`](fixture_splits.py),
> and `tests/test_holdout_partition.py`.

This document fixes the analysis plan for a held-out evaluation of the ReClass
ACMG/AMP scoring engine. It targets the two objections a reviewer raises first:

1. **In-sample leakage** — "were the thresholds tuned on the same variants you report
   concordance on?"
2. **Post-hoc goalposts** — "were the acceptance bars chosen after seeing the result?"

**Leakage is answered structurally and holds unconditionally:** the configuration is
**locked and hash-pinned**, and a **reserved 30% holdout sub-split** is carved by a
deterministic, label-blind rule that the calibration tooling is forbidden from
loading. Held-out concordance is therefore out-of-sample with respect to any tuning.

**On post-hoc goalposts this registration is only *partially* blinded, and that limit
is disclosed here rather than overclaimed** (see *Disclosure* below). Only the
**primary gating bars (H1: 0.85 / <0.01)** are genuinely a priori — they have lived in
`harness.py` since the project's initial commit (2026-06-16), predating every
benchmark result, and are merely restated here with confidence-interval rigor. The
**secondary thresholds (H3 ≥ 15 pp contrast; 3 pp overfit guardrail) are not blinded**:
they were set after population-level results on the full fixtures were already
computed and committed, so they are *descriptive* thresholds informed by the observed
effect magnitudes, not pre-data predictions. The held-out *subset* point estimates
reported below were unseen until evaluation; the population effect *sizes* were not.

---

## Disclosure — what was and was not blinded at registration

Stating the registration timeline plainly matters more than a stronger-sounding claim;
a reviewer checking `git log` will reconstruct exactly this:

- **Visible before this registration.** Population-level results on the *full* fixtures
  were already computed and committed (`validation_report.md`, commit `6a41bf5`,
  2026-06-23) ~17 minutes before this plan was committed (`1bb7ed9`): ClinGen definitive
  concordance ≈ 94.7%, raw ClinVar ≈ 5.0%, enriched ClinVar ≈ 42.4%, enrichment lift
  ≈ +37.4 pp. The *magnitudes* of every effect this plan reports were therefore known.
- **Genuinely a priori (H1 only).** The H1 gating bars (0.85 definitive-concordance
  floor, <0.01 serious-discordance ceiling) predate all data — committed in `harness.py`
  at project inception (2026-06-16). H1 is the only hypothesis whose acceptance bars were
  fixed before any result existed.
- **Genuinely unseen until evaluation.** The held-out *sub-split* point estimates and
  their confidence intervals — the numbers reported below — were not computed until the
  locked evaluation run. The split is label-blind and deterministic, so which variants
  land in holdout could not be chosen to flatter the result.
- **Not blinded (H3, overfit guardrail).** The H3 ≥ 15 pp contrast threshold and the
  3 pp overfit guardrail were set with the population effect sizes above already visible.
  They are reported as descriptive, comfortably-cleared thresholds — **not** blind
  a-priori predictions — and no claim here depends on treating them as such.

---

## Locked configuration (committed before this plan)

| Field | Value |
|---|---|
| Engine version | `1.0.0` |
| Config hash | `b8c5a5f4be24f83d8904912c362ec6f73a3760bd5a8963d2e3687fd0020f41ed` |
| Config label | ACMG/AMP Bayesian points (base) |

`engine.config.config_fingerprint()` **must** equal this hash at evaluation time.
The evaluator aborts otherwise — a changed config voids this registration.

---

## The split rule (deterministic, blind, cross-fixture-consistent)

A case is assigned to **holdout** iff

```
int( sha256("reclass-holdout-partition-v1" + ":" + identity).hexdigest()[:12], 16 ) % 1_000_000
    < round(0.30 * 1_000_000)
```

- **identity** = `GRCH38-{chrom}-{pos}-{ref}-{alt}` (uppercased) when the genomic
  locus is complete, else `ID:{case id}`.
- **Blind.** The identity is derived only from the variant locus/id — never from the
  expected label or any engine output — so membership is statistically independent of
  the outcome being measured. (Verified: holdout and development label distributions
  match within ~1 pp on every benchmark.)
- **Cross-fixture-consistent.** Keyed on the genomic locus, the *same physical
  variant* is reserved in every fixture it appears in. No test variant can have
  influenced a threshold through another fixture. The two ClinVar fixtures therefore
  share one holdout fingerprint.
- **Deterministic.** SHA-256, no RNG and no wall-clock — the partition is
  byte-reproducible and citable by fingerprint.

### Pinned holdout partition (re-verified at evaluation time)

| Benchmark | Total | Holdout | Holdout % | Holdout SHA-256 (prefix) |
|---|---:|---:|---:|---|
| `clingen_real_v1` | 12,446 | 3,635 | 29.2% | `a09e10e1673070…` |
| `clinvar_real_v1` | 21,638 | 6,487 | 30.0% | `6b37ea826a5dc8…` |
| `clinvar_enriched_v1` | 21,638 | 6,487 | 30.0% | `6b37ea826a5dc8…` |

---

## Hypotheses & frozen acceptance criteria

### H1 — Primary (gating)

> On expert-curated variants whose loci the locked configuration was never exposed
> to, the engine reproduces ClinGen expert-panel **definitive** classifications with
> high concordance and near-zero serious discordance.

Evaluated on **`clingen_real_v1` holdout**. **Pass requires both:**

| Endpoint | Definition | Bar |
|---|---|---|
| Definitive concordance | exact-tier match among non-VUS expected cases | Wilson **lower** 95% bound **≥ 0.85** |
| Serious discordance | fraction of holdout cases with a P↔B tier flip | Wilson **upper** 95% bound **< 0.01** |

Using the *CI bound* rather than the point estimate is the rigorous form of the
committed gate: it must hold even at the unfavourable edge of sampling error.

### H2 — Negative control (descriptive)

> Sparse public ClinVar signals alone do **not** reproduce expert definitive calls.

Evaluated on **`clinvar_real_v1` holdout**. Pre-stated expectation: **fails** the 0.85
bar. Reported, not gated.

### H3 — Evidence-completeness contrast (the scientific claim)

> Adding matched ClinGen-applied criteria materially raises held-out definitive
> concordance over sparse ClinVar — evidencing that **evidence completeness, not
> scoring logic, is the binding constraint.**

Evaluated on the **`clinvar_real_v1` → `clinvar_enriched_v1` holdout** pair.
Expectation: definitive concordance rises by **≥ 15 pp**. *Descriptive threshold — set
with the population-level +37.4 pp lift already visible (see Disclosure); not a blind
a-priori prediction.*

### Overfitting guardrail (all benchmarks)

Flag the configuration as overfit to development variants if

```
dev_definitive_concordance − holdout_definitive_concordance > 0.03  (3 pp)
```

A held-out number close to the development number is itself evidence that the locked
thresholds are not tuned to specific variants. *The 3 pp tolerance is descriptive — set
with development-vs-holdout gaps (≤ 2.2 pp) already observed; see Disclosure.*

---

## Analysis plan

- **Single scoring pass.** Each holdout case is scored once under the locked config —
  no per-case tuning, no threshold search, no perturbed re-scoring.
- **Metrics.** `validation.harness.compute_metrics` (identical definitions to the
  committed gate) plus Wilson 95% intervals computed in `holdout_eval.py`.
- **Stratification.** Definitive concordance and serious counts are reported per
  VCEP/panel group and per ancestry stratum where present; a pooled figure is never
  shown alone.
- **No peeking.** Calibration/threshold tooling drops holdout-partitioned cases at
  load time (`validation.calibration`), enforced by a test.
- **Reproducibility.** Re-running the deterministic split must reproduce the pinned
  fingerprints above.

## Amendment policy

Changing the locked config, the split salt/fraction/identity basis, or any acceptance
threshold requires **incrementing the registration version** and re-pinning the
fingerprints. Results obtained under a prior version may not be re-reported under a
new one.
