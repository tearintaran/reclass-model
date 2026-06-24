# ReClass Held-Out Validation — Pre-Registration v1

> Status: **registered** · 2026-06-23
> Machine-readable contract: [`preregistration.json`](preregistration.json)
> Enforced by: [`holdout_eval.py`](holdout_eval.py), [`fixture_splits.py`](fixture_splits.py),
> and `tests/test_holdout_partition.py`.

This document fixes the analysis plan for a **blinded, pre-registered held-out
evaluation** of the ReClass ACMG/AMP scoring engine *before* the held-out numbers
are looked at. Its purpose is to make the headline concordance figures defensible
against the two objections a reviewer will raise first:

1. **In-sample leakage** — "were the thresholds tuned on the same variants you report
   concordance on?"
2. **Post-hoc goalposts** — "were the acceptance bars chosen after seeing the result?"

Both are answered structurally: the configuration is **locked and hash-pinned**, a
**reserved holdout sub-split** is carved by a deterministic rule that calibration is
forbidden from seeing, and the **acceptance criteria below are frozen** (they adopt
the bars already committed in `harness.py`, now strengthened with confidence
intervals).

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
Pre-stated expectation: definitive concordance rises by **≥ 15 pp**.

### Overfitting guardrail (all benchmarks)

Flag the configuration as overfit to development variants if

```
dev_definitive_concordance − holdout_definitive_concordance > 0.03  (3 pp)
```

A held-out number close to the development number is itself evidence that the locked
thresholds are not tuned to specific variants.

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
