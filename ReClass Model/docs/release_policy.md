# Clinical release policy

ReClass output is a draft classification until a credentialed human reviewer
approves it. Engine output alone is never clinically releasable.

## Minimum release gates

A draft classification may become clinically releasable only when all gates pass:

| Gate | Requirement |
|---|---|
| Identity | Variant identity is normalized, assembly-specific, and matches the reportable variant. |
| Evidence | Every applied ACMG/AMP criterion has source provenance, source version, and reviewer-readable support. |
| Scope | The variant, gene, disease, inheritance mode, and evidence type are inside the signed clinical scope. |
| Configuration | `engine/configs/base_v1.json` and PS4 rules have credentialed sign-off for the active config hash and commit. |
| Conflict review | BA1/BS1, curated pathogenic evidence, and founder-frequency exceptions are resolved under `docs/conflict_handling.md`. |
| Validation | Required validation/calibration/reanalysis reports are current under `docs/release_review.md`, with no unresolved serious discordance in the relevant scope. |
| Reviewer | A credentialed reviewer records sign-off with name, credential, date, tier, scope, and any caveats. |
| Audit | The sign-off and source evidence receipts are persisted in the clinical audit trail. |

## Release states

| State | Meaning | Patient-facing? |
|---|---|---|
| `draft` | Engine-generated classification without human approval. | No |
| `review_pending` | Evidence packet is assembled and ready for clinical review. | No |
| `approved_for_release` | Credentialed reviewer signed the classification and all gates passed. | Yes |
| `released` | Approved classification has been transmitted through the lab's release workflow. | Yes |
| `withdrawn` | Approval was revoked because evidence, identity, scope, or policy changed. | No new release; prior recipients require local correction workflow. |

## Required reviewer checks

Before sign-off, the reviewer must confirm:

- the reported disease and mode of inheritance match the applied VCEP/specification;
- no applied criterion is counted twice through correlated evidence;
- population-frequency evidence uses the correct population metric and denominator;
- PM2 prerequisites are present when a VCEP PS4 proband rule requires them;
- any downgrade or upgrade from automated output is explained in the report;
- any limitation, excluded evidence class, or unresolved VUS rationale is visible.

## Re-review triggers

An approved classification returns to `review_pending` when any of the following
changes:

- source data version used by an applied criterion;
- engine version, config hash, VCEP override, or PS4 rule;
- variant identity, transcript, disease assertion, or mode of inheritance;
- ClinVar/ClinGen conflict status or curated expert-panel interpretation;
- local conflict policy or release scope;
- validation report with a serious discordance in the affected scope.
