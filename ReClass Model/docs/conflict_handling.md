# Conflict handling policy

This policy governs conflicts between benign population-frequency evidence
(`BA1`, `BS1`) and curated pathogenic evidence, including known founder variants.

## Default rule

BA1 is a stand-alone benign criterion and normally forces a benign classification.
BS1 is strong benign evidence. Neither should be applied mechanically when curated
variant-specific evidence shows that the frequency signal is not interpretable as
benign for the asserted disease, inheritance mode, and population.

## Conflict triage

When BA1/BS1 conflicts with curated pathogenic or likely pathogenic evidence:

| Step | Action |
|---|---|
| 1 | Confirm variant identity, assembly, transcript, and normalization. |
| 2 | Confirm the disease, inheritance mode, penetrance, and VCEP scope. |
| 3 | Recompute frequency using the VCEP-required population metric and denominator. |
| 4 | Check whether the high frequency is population-specific, founder-associated, low-quality, or due to a mismapped/complex allele. |
| 5 | Review curated pathogenic evidence independently; do not let a prior label substitute for evidence. |
| 6 | If the conflict remains unresolved, hold the classification at `review_pending` or VUS until a credentialed reviewer records a disposition. |

## Founder-frequency exceptions

Founder exceptions are allowed only as explicit per-variant approvals. A valid
exception record must include:

- canonical `variant_key`;
- disease, gene, transcript, inheritance mode, and founder population;
- evidence that the allele is a known founder pathogenic variant in that context;
- the population database, version, subpopulation, allele count, allele number, and
  frequency/FAF value that triggered BA1/BS1;
- the exact threshold override, if any;
- reviewer name, credential, date, expiry/re-review date, and source citations.

`base_v1.json` intentionally contains only
`founder_variant_frequency_exception_template` with an empty `set` block. It has no
active scoring effect. Real founder exceptions must replace the template with a
signed, variant-specific override before use.

## Configuration impact

Frequency conflicts may be handled in one of three ways:

| Outcome | Config action |
|---|---|
| Frequency evidence is valid | Keep BA1/BS1 as applied. |
| Frequency evidence is invalid for technical or scope reasons | Do not apply BA1/BS1; document the excluded evidence in the review packet. |
| Founder exception is approved | Add a per-variant override in `base_v1.json` with reviewer-approved thresholds and source annotations. |

No broad gene-wide founder exception is permitted unless a current VCEP
specification explicitly defines it and the local clinical sign-off records that
scope.
