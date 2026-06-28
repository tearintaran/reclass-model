# Operational standard operating procedures

These SOPs describe how to **operate** the ReClass reanalysis runtime and API
review workflow. They do not change clinical scoring rules (those live in the
engine config and are governed separately).

## Roles

| Role | Typical holder | Responsibilities |
|---|---|---|
| Operator | Bioinformatics / LIMS engineer | Schedule reanalysis runs, monitor queue health |
| Reviewer | Credentialed clinical scientist | Review drafts, sign off classifications, triage alerts |
| Admin | Lab director / QA lead | Release validation, approve config changes |

## 1. Case worklist

The reviewer UI opens on the tenant-scoped worklist. Cases represent ordered
specimens and link to one or more classification receipts.

Daily workflow:

1. Filter by status, priority, assignee/unassigned, SLA state, or accession search.
2. Assign cases individually or with the bulk assignment action.
3. Move cases only through allowed transitions: the main path is
   `draft → in_review → signed → released`; `on_hold` and `cancelled` are side
   states, and released cases may reopen to `in_review` for amendment.
4. Use bulk transition for mixed selections; review the per-case result because
   legal cases succeed while illegal transitions are reported without rolling back
   the whole selection.
5. Link persisted classification receipts to the case before sign-off/release.
6. Review overdue and due-soon metrics at the start and end of the shift.

Worklist list/detail responses are de-identified by default. Requesting patient
MRN, patient name, or indication requires `case:read_phi`; every PHI read is
audited. Do not copy PHI into research evidence, webhook payloads, logs, or
validation fixtures.

## 2. Reanalysis runs

### When to run

Trigger reanalysis when any of the following change:

- **Evidence** — new curated submissions or provider cache refresh
- **Provider version** — gnomAD, REVEL, or ClinGen fixture version bump
- **Config version** — engine/scoring configuration update (after clinical sign-off)

Use `ops/scheduler.py` trigger detection helpers to identify affected variants,
then enqueue work via `ops/queue.py`.

Each tenant must have a reanalysis policy covering cadence, included sources,
affected scope, escalation thresholds, and retention. The default policy is
monthly, includes ClinVar/ClinGen/gnomAD/REVEL, escalates serious crossings as
critical, and retains run reports for seven years unless local policy overrides it.

### Dry run (no database)

Load a manifest and execute in memory:

```bash
python -c "
from ops import queue, scheduler
items = queue.load_manifest('path/to/manifest.json')
# wire resolve_events + run_one per your environment
"
```

### Production run (database-backed)

1. Enqueue variants: `ops.queue.enqueue(cur, tenant_id=..., variant_id=..., trigger='provider_version', reason='gnomAD 4.1')`
2. Execute: `ops.scheduler.run_from_queue(cur, tenant_id=..., resolve_events=...)`
3. Read the run report from `clinical.reanalysis_run` or the returned `RunReport`
4. Review operator views for queue state, run manifests, failed/skipped reason
   codes, provider-cache readiness, and same-tier changes
5. Triage **crossed** outcomes first (tier changes → alerts)
6. Review **failed** items by `last_reason_code` (missing cache, invalid identity, etc.)
7. **Skipped** items with `no_evidence` may need manual evidence curation

### Expected outcomes

| Outcome | Operator action |
|---|---|
| unchanged | No action (churn guard) |
| same_tier | Audit only — no alert |
| crossed | Alert created — assign to reviewer |
| failed | Fix root cause (cache, reference) and requeue |
| skipped | Confirm evidence availability |

## 3. Alert review

Tier-crossing alerts appear in `GET /alerts` and the reviewer UI **Alerts** tab.

### Triage order

1. **Serious crossings** (`serious: true`) — e.g. VUS → Pathogenic or Benign → Pathogenic
2. Open alerts by age (oldest first)
3. Group by variant for batch review

### Lifecycle states

```
open → acknowledged → in_review → resolved
                                 → dismissed
```

- **acknowledged** — reviewer has seen the alert
- **in_review** — active investigation underway
- **resolved** — tier change reviewed and action taken (re-sign-off, report amendment, etc.)
- **dismissed** — alert deemed non-actionable (document rationale in lab LIMS)

Illegal transitions return HTTP 409. All state changes are audit-logged.

Triage metadata must be filled before resolution:

- owner;
- SLA due date;
- severity (`low`, `standard`, `high`, `critical`);
- resolution rationale;
- re-review outcome;
- notification state (`not_required`, `pending`, `sent`, `acknowledged`, `failed`).

## 4. Credentialed sign-off

A persisted classification starts as a **draft** (`is_draft: true`). It is not
clinically releasable until sign-off.

### Reviewer workflow

1. Resolve evidence (`POST /evidence/resolve`) and inspect warnings
2. Preview classification (`POST /classify`) — stateless, not stored
3. Persist draft (`POST /classifications`)
4. Open technical reviewer report (`GET /classifications/{id}/report/reviewer`)
5. Verify criteria contributions, provenance, and any reanalysis history
6. Build/review the release gate packet (`/validation/release-gate`)
7. Approve release only after all blockers clear

Sign-off is recorded in the audit log with actor, timestamp, signer identity,
active scope, config hash, commit, source snapshots, validation-report id,
conflict disposition, credential, authorization, effective date, and re-review date.

### Policy gates (local lab must define)

- Minimum evidence completeness before sign-off
- Second reviewer requirement for Pathogenic / Likely Pathogenic tiers
- Conflict resolution (e.g. BA1 vs curated pathogenic founder variants)

## 5. Patient-safe summary release

The patient summary (`GET /classifications/{id}/report/summary`) must only be
released **after** credentialed sign-off.

### Release checklist

- [ ] Classification is signed off (`is_draft: false`)
- [ ] Reviewer report reviewed and archived
- [ ] Tier-crossing alerts for this variant are resolved or dismissed
- [ ] Summary language reviewed for patient comprehension (no internal codes)
- [ ] Release logged in LIMS / EHR per local policy

### Do not release when

- Draft status (`is_draft: true`)
- Open serious alert on the same variant without documented resolution
- Known evidence gaps flagged in reviewer report warnings

## 6. Amended FHIR reports and clinician notification

Amended reports use the FHIR outbound adapter around the signed classification.
Each lifecycle record tracks report id, previous report id, amended/final state,
amendment reason, payload hash, and notification state. Clinician notifications
track recipient, channel, state, and rationale.

For LIS/EHR workflows:

1. Render the final outbound FHIR payload and archive its SHA-256.
2. On amendment, render a new payload with `previous_report_id` and amendment
   reason.
3. Create clinician notification rows for intended recipients.
4. Update notification state as delivery/acknowledgement occurs.

## 7. Audit and retention

Operational audit entries (`GET /audit`) cover:

- `classification.create`
- `classification.sign_off`
- `alert.state_change`
- `reanalysis.run`
- `reanalysis.policy_update`
- `classification.release_approved`
- `classification.release_state_change`
- `alert.triage_update`
- `report.amended`
- `case.create`, `case.update`, `case.transition`, `case.attach_classification`
- `case.bulk_assign`, `case.bulk_transition`, `case.read_phi`

Retain audit logs per institutional policy (recommended minimum: 7 years for
clinical genomics). Database retention is configured at the PostgreSQL level;
in-memory audit is for development only.

## 8. Incident response

| Symptom | Likely cause | Action |
|---|---|---|
| HTTP 503 on clinical endpoints | PostgreSQL unavailable | Fail over / restore DB; check `/health` |
| Reanalysis failures: `missing_provider_cache` | Stale or absent provider cache | Refresh cache; requeue failed items |
| Reanalysis failures: `unavailable_reference` | GRCh38 FASTA missing | Install reference per `data/reference/README.md` |
| Unauthorized (401) in production | Missing/expired Bearer token | Re-issue token; verify OIDC issuer/audience/JWKS settings or HS256/API-key fallback config |
| Forbidden (403) | Insufficient role | Grant appropriate role in token/API key |

## References

- API authentication: [auth.md](auth.md)
- Deployment and backups: [deployment.md](deployment.md)
- Release validation reports: [release_review.md](release_review.md)
