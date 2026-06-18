# Clinical configuration review

This file is the review ledger for `engine/configs/base_v1.json` and the cohort
PS4 rules in `monitoring/reanalysis.py`.

Status on 2026-06-16: governance reviewed, not clinically signed. ReClass remains
research / decision-support only until a credentialed clinical reviewer records
approval below.

## Current configuration

| Field | Value |
|---|---|
| Config file | `engine/configs/base_v1.json` |
| Version | `1.0.0` |
| Config hash | `b8c5a5f4be24f83d8904912c362ec6f73a3760bd5a8963d2e3687fd0020f41ed` |
| Review record in config | `clinical_review.review_status = governance_reviewed_pending_credentialed_signoff` |
| Clinical release state | Blocked until credentialed human sign-off |

## Review ledger

| Date | Reviewer | Qualification | Scope | Outcome |
|---|---|---|---|---|
| 2026-06-16 | Codex automated governance pass | Not a qualified clinical reviewer | Source/spec drift check; config annotation; policy documentation; repo guard wiring | Governance reviewed only. PAH override confirmed against PAH CSpec v2.0.0. Hearing Loss GJB2 frequency override corrected to CSpec v2.0.0 thresholds. Founder exception converted to a non-scoring template. Cardiomyopathy PS4 proband shortcut removed because current CSpecs require OR confidence-interval evaluation. Hearing Loss proband-count PS4 limited to autosomal-dominant genes that this count-only helper can represent. |
| 2026-06-17 | Codex project-alignment pass | Not a qualified clinical reviewer | Documentation alignment to implemented OR/CI PS4 support and current test/validation state | Documentation updated to reflect denominator-aware Cardiomyopathy PS4 odds-ratio / 95% CI support in `monitoring/reanalysis.py`. This is code support only; credentialed clinical sign-off of thresholds and scope remains pending. |
| Pending | Credentialed clinical reviewer | Lab director, ABMGG/CCMG equivalent, or locally authorized signatory | Full clinical use of scoring defaults, VCEP overrides, PS4 rules, release policy, and conflict policy | Required before any patient-facing use. |

## VCEP override review

| Override | Current setting | Source checked | Review outcome |
|---|---|---|---|
| `pku_vcep_ba1` | `ba1_af = 0.015`, `bs1_af = 0.002` | ClinGen Phenylketonuria Expert Panel PAH CSpec v2.0.0, released 2024-07-16 | Confirmed 2026-06-16. |
| `hearing_loss_gjb2_35delg` | `ba1_af = 0.005`, `bs1_af = 0.003` | ClinGen Hearing Loss Expert Panel CSpec v2.0.0, released 2022-03-30 | Corrected 2026-06-16 from the prior illustrative `bs1_af = 0.005` setting. |
| `founder_variant_frequency_exception_template` | No scoring thresholds in `set` | Local conflict policy | Template only. A real exception requires a variant-specific signed review record. |

## PS4 cohort rule review

| Rule | Current behavior | Source checked | Review outcome |
|---|---|---|---|
| Generic case-control default | `min_cases = 5`, `min_enrichment = 5.0`, strength by affected case count | ACMG/AMP PS4 concept and ClinGen SVI points framework | Conservative fallback only; not a substitute for VCEP-specific statistical review. |
| Hearing Loss autosomal-dominant proband-count rule | 2/6/15 unrelated probands -> supporting/moderate/strong, with PM2 supplied separately | ClinGen Hearing Loss CSpec v2.0.0 PS4 | Kept for `COCH`, `KCNQ4`, and `MYO6` pending local sign-off. Recessive genes such as `GJB2` fall back to the default. |
| Cardiomyopathy genes | No proband-count shortcut; denominator-aware OR/CI rule when case/control totals are supplied | Current ClinGen Cardiomyopathy CSpecs, including ACTC1 v1.0.0 released 2024-04-22 | Historical 2/6/15 proband shortcut is not encoded for cardiomyopathy. `PS4OddsRatioRule` computes a case/control odds ratio with a Wald 95% CI and maps the lower bound to reviewable strength bins. Threshold values and scope still require credentialed current-spec sign-off before clinical use. |

## Clinical sign-off requirements

A credentialed sign-off must record:

- reviewer name, credential, role, and institution or lab authorization;
- exact config hash and code commit reviewed;
- source/spec versions and access dates reviewed;
- scope of approval, including any excluded genes, diseases, or variant classes;
- required validation reports and acceptance criteria;
- effective date, expiry/re-review date, and change-control ticket.

No draft classification can become clinically releasable unless the release policy
in `docs/release_policy.md` is also satisfied.
