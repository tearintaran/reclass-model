# ReClass Reference Model

Audience: medical practitioners, clinical laboratory professionals, geneticists,
variant scientists, and researchers who want to understand what ReClass can do,
what information it accepts, how it uses that information, and what it produces.
This is not a programmer guide. Where a clinical or scientific reader benefits
from a pointer, the relevant repository location is named, but no coding knowledge
is assumed.

---

## Clinical Status

ReClass is a local **proof of concept** for auditable, reproducible variant
reclassification. It is **decision support only**.

It is **not** FDA-cleared, CLIA-validated, or suitable for autonomous patient
reporting. A qualified human reviewer remains responsible for evidence assessment,
interpretation, sign-off, and any clinical release. ReClass does not diagnose,
recommend treatment, estimate penetrance, or make patient-management decisions.

The reconstructed scoring configuration has been through an internal **governance
review** but its formal clinical-release state is recorded in the repository as
`governance_reviewed_pending_credentialed_signoff`. In plain terms: the rules have
been checked against published specifications and corrected where they had drifted,
but **a credentialed clinical reviewer has not yet signed them off**, and no output
is releasable to a patient until that and the other gates in
[`roadmap.md`](roadmap.md) are met.

Latest project review: **2026-06-19**. Local checks showed the proof-of-concept
engine and service scaffold are stable: 877 tests passed, `ruff` and scoped `mypy`
passed, the reviewer frontend browser harness passed 52/52 checks, and the local
GRCh38 cache was loadable with matching metadata. Thirty-one PostgreSQL-backed
storage/RLS tests skipped locally because no PostgreSQL server was running; the CI
workflow is configured to run PostgreSQL-backed checks.

The 2026-06-19 review also defined and the project has since **built** a
scalable-product feature layer on top of the engine: an evidence workbench and
evidence-coverage operations, enforced release-gate sign-off, continuous-reanalysis
operations, enterprise deployment/security hardening, and customer-facing
integration surfaces. These features make the engine usable, governable, and
supportable at scale; they do **not** change the scoring math and do **not** remove
the clinical, regulatory, data-licensing, and credentialed-sign-off gates that
remain in [`roadmap.md`](roadmap.md) and [`gap.md`](gap.md).

---

## What ReClass Does

ReClass takes structured variant evidence, applies a deterministic ACMG/AMP-style
point model, and returns one of the standard five tiers:

- Pathogenic
- Likely Pathogenic
- Variant of Uncertain Significance (VUS)
- Likely Benign
- Benign

The output is not just a tier. Each result is an **auditable receipt** that records
the evidence used, the points contributed by each item, source versions, warnings,
review status, engine/configuration version, and a reconstruction hash that lets
the classification be verified later, byte for byte.

The same engine can run four ways:

1. **As a calculator** — feed it structured evidence and read the tier and receipt.
2. **As a validation harness** — replay it across thousands of benchmark variants
   and measure how well it reproduces expert reference labels.
3. **As a clinical-style operations service** — resolve evidence, classify, persist
   a draft, capture reviewer-entered evidence in an **evidence workbench**, see
   **evidence-coverage** and **curation** queues, import variants and evidence in
   bulk, generate reviewer and patient-safe reports, enforce a structured
   **release-gate sign-off**, export a **validation packet**, continuously re-flag
   variants when evidence changes, triage the resulting alerts, track amended
   reports and clinician notifications, and deliver outbound webhooks — all through
   an API, a clinician-facing reviewer web page, and an evidence-workbench page.
4. **As a command-line tool** — a `reclass` operator command classifies a single
   variant, runs a validation benchmark, checks the reference-genome cache,
   compares before/after benchmark runs, runs calibration, and regenerates the
   analytical-validation and failure-analysis reports.

---

## Current Capabilities

ReClass currently supports:

**Scoring and identity**

- Deterministic ACMG/AMP-style scoring from structured criteria and selected source
  signals, using a Tavtigian/ClinGen SVI point model.
- A versioned scoring configuration with reviewable VCEP/gene/disease overrides.
- Canonical variant identity using both source-style provider keys such as
  `1-100-A-G` and storage-compatible keys such as `GRCh38-1-100-A-G`.
- Reference-free variant normalization, plus reference-backed indel left-alignment
  using a locally installed GRCh38 reference genome.
- Multiple identity-matching routes for linking a variant to expert evidence:
  ClinVar Variation ID, ClinVar Allele ID, NCBI SPDI, canonical SNV key,
  MANE Select / coding-HGVS transcript identity, genomic-HGVS, and reference-backed
  indel matching. Routes are applied in a fixed priority order, and when more than
  one expert record matches with conflicting criteria the match is **flagged as
  ambiguous and no evidence is imported**, rather than silently picking one.
- MANE Select transcript identity (RefSeq/Ensembl, gene, coding and protein HGVS)
  carried through ingest into the evidence bundle and surfaced in reports.

**Evidence providers**

- ClinGen Evidence Repository criteria through a reusable evidence provider.
- REVEL missense computational evidence through a reusable evidence provider.
- gnomAD allele-frequency evidence through a reusable evidence provider with local
  caching.
- AlphaMissense, conservation, and gene-constraint context through offline-testable
  computational providers. REVEL and AlphaMissense are resolved into a single
  documented computational call when both are present, so predictors are not
  double-counted as independent PP3/BP4 evidence.
- An **extended evidence layer** with offline-tested providers for additional
  ACMG/AMP criteria: loss-of-function (PVS1), functional assays (PS3/BS3,
  OddsPath-calibrated), in-trans observations (PM3), segregation (PP1/BS4),
  phenotype specificity (PP4), splice impact, copy-number/dosage (CNV → PVS1 or
  PM4), non-coding/regulatory evidence, complex indels, mitochondrial signals,
  repeat expansions, and richer structural-variant signals. These accept
  structured inputs a reviewer or upstream pipeline supplies; the engine does not
  derive them from raw biology on its own.
- A set of **upstream-evidence adapters** for de novo (PS2/PM6), allelic phasing
  (PM3/BP2), segregation (PP1/BS4), phenotype specificity (PP4), functional assays
  (PS3/BS3), disease mechanism (PP2/BP1), and case-control enrichment (PS4). Each
  adapter records the source version, content checksum, and access date; emits an
  explicit "absent" or "malformed" record instead of guessing when the input is
  missing or unusable; and never reaches the network during testing.
- Reproducible source-cache builders for AlphaMissense, conservation, gene
  constraint, and functional/phenotype evidence. Each writes a manifest (source
  version, checksum, access date) and regenerates byte-for-byte from the same
  inputs.
- ClinVar-to-ClinGen enrichment by direct ClinVar Variation ID, canonical SNV key,
  and genomic-HGVS fallback routes when source identity fields are available (now
  contributing measurable real-data lift).
- Cohort-count PS4 evidence using published ClinGen VCEP proband-count rules for
  supported gene sets, with a conservative fallback elsewhere.
- Evidence bundles that preserve provider versions, source records, warnings,
  match type, and raw provenance.

**Workflow, storage, and operations**

- Tenant-aware persistence for classifications, evidence events, evidence bundles,
  cohort counts, reanalysis events, alerts, and sign-off state.
- A reviewer workflow in which persisted classifications remain **drafts** until
  credentialed sign-off.
- A clinician-facing **reviewer web application** that drives the whole workflow
  (resolve evidence → classify → review draft → view reports → sign off → triage
  alerts) against the API, presenting structured, readable views of evidence, point
  contributions, warnings, prior history, and draft-vs-signed state rather than raw
  data, with explicit loading/error/empty states and a small viewport layout.
- Technical reviewer reports and patient-safe summary reports.
- Role-based access control, bearer-token/API-key authentication (including
  RS256/JWKS OIDC), audit logging of sign-off and alert/reanalysis actions, and
  health/metrics endpoints.
- A **pinned API contract**: the live service schema is checked against a stored
  OpenAPI artifact (drift fails continuous integration), with runnable cookbook
  examples for the classify, evidence-resolution, sign-off, report, alert, and
  reanalysis flows.
- A deterministic **FHIR Genomics export** with amended-report state transitions
  (draft → final → amended) and replayable outbound payloads that reproduce
  byte-for-byte, as integration scaffolding for an LIS/EHR.
- **Production-readiness preflight checks** that fail at startup with a clear,
  named message (not a stack trace) when a required prerequisite is missing:
  environment variables, OIDC/JWKS configuration, audit backend, database role,
  reference-FASTA metadata, or provider-cache manifests.
- A documented containerized deployment path with database backup procedures.
- Continuous reanalysis support with queueing, run reports, same-tier audit events,
  and tier-crossing alerts. **Change-control triggers** — a source snapshot,
  provider version, configuration version, or conflict-policy change — automatically
  enqueue the affected variants together with an auditable run manifest that records
  the trigger cause and a run id.

**Evidence workbench, coverage, and import**

- An **evidence workbench** where a reviewer or upstream pipeline enters structured
  ACMG/AMP evidence the public scores do not encode — `PVS1`/loss-of-function,
  `PS3`/`BS3` functional assays, `PM3` phasing, `PP1`/`BS4` segregation, `PP4`
  phenotype/HPO matching, `PS4` cohort/case-control, and `BA1`/`BS1` benign-frequency
  review. Each entry is persisted with full provenance: reviewer identity and
  credential, source and source version, a content checksum, the access date, an
  active/expired/superseded/withdrawn status, and an expiry / re-review date. Entries
  are kept in the de-identified research domain and flow into scoring as standard
  evidence events.
- **Evidence-coverage dashboards** that measure, for each case, which evidence
  categories are present versus the categories its variant class should have, mark a
  case **blocked** when a blocking category (loss-of-function, functional,
  segregation, case-control, phasing) is missing, and roll the result up by gene,
  VCEP, disease, variant class, and provider so operators can see where missing
  evidence is concentrated.
- **Curation queues** that surface evidence problems needing human triage rather
  than missing data: unmatched or ambiguous source identity, a transcript-dependent
  criterion with no named transcript, a `PS4` claim with no cohort denominator, and
  variants carrying both pathogenic and benign evidence. Each item has a kind,
  severity, and an open / in-review / resolved / dismissed state.
- **Validated batch and file import.** Upstream functional, phenotype, family,
  cohort, and lab evidence can be bulk-imported through the existing adapters with
  patient identifiers scrubbed before anything reaches the research tables, and
  **VCF/CSV/TSV variant import** normalizes identity, splits multiallelic sites,
  left-aligns indels, detects duplicates, optionally previews evidence resolution,
  and returns a **dry-run report** before anything is persisted.

**Release governance and reanalysis operations**

- An enforced **release-gate state machine** with explicit states
  `review_pending`, `approved_for_release`, `released`, `withdrawn`, and
  `re-review_required`, with validated transitions between them.
- Structured **sign-off packets** that must carry the signed clinical scope, engine/
  config hash, code commit, source snapshots, validation-report id, conflict-policy
  disposition, reviewer credential, institutional authorization, effective date, and
  re-review date. Sign-off is **blocked** when the variant/gene/disease/evidence
  class is out of the signed scope, when a required field or preflight check fails,
  when the active config hash does not match the packet, or when a relevant serious
  pathogenic/benign discordance is still unresolved.
- An **exportable validation/release packet** that bundles the validation-report id,
  release scope, config hash, source snapshots, benchmark metrics, the
  serious-discordance disposition, and the full sign-off ledger under a deterministic
  packet id.
- **Reanalysis operator views** exposing queue status, run manifests (checked,
  unchanged, same-tier, crossed, failed, skipped), failed/skipped reason codes,
  provider-cache readiness, and same-tier changes, plus per-tenant **reanalysis
  policies** (cadence, included sources, affected scope, escalation thresholds, and
  retention).
- An **alert-triage workflow** in which each tier-crossing alert carries an owner, an
  SLA due date, a severity (low/standard/high/critical), a resolution rationale, a
  re-review outcome, and a notification state, and moves through open / acknowledged
  / in-review / resolved / dismissed states.
- **Amended-report and clinician-notification tracking** around the FHIR Genomics
  serializer (draft → final → amended), with an LIS/EHR amended-report lifecycle
  adapter and a notification roster (recipient, channel, state).

**Platform, security, and integration**

- A **fail-closed production preflight** that refuses to start unless required
  prerequisites pass — environment configuration, OIDC/JWKS, persistent audit
  backend, database role and row-level-security policies, reference-FASTA metadata,
  provider-cache manifests, and a consistent migration ledger — and a readiness/
  health report that surfaces the same checks.
- A **production auth mode** that accepts only RS256/JWKS OIDC bearer tokens and
  disables the HS256 JWT and static API-key fallbacks.
- **Rate limiting**, **request-size limits**, an **audit-retention policy**, and
  **structured security events** recorded in the audit log.
- **Service-level-objective metrics** for API latency, provider-cache freshness,
  reanalysis lag, failed evidence resolution, alert backlog, and restore-test
  freshness, exposed for scraping.
- A **webhook delivery subsystem** with endpoint registration, HMAC-signed payloads,
  retry with backoff, and delivery tracking for tier crossings, source-snapshot
  updates, configuration changes, and completed reanalysis runs.
- **Tenant administration and onboarding** tools (tenant records, source-cache
  setup, reference-cache verification, OIDC setup checks, a sample-data smoke test,
  and a pre-production readiness report), plus a **typed client generated from the
  pinned OpenAPI contract** and a customer-facing API cookbook.

**Validation and governance**

- Validation on synthetic, ClinGen, raw ClinVar, and ClinVar-plus-ClinGen
  benchmarks.
- Failure-analysis, before/after comparison, calibration, and diagnostic-plot
  reports.
- **Development / validation / holdout fixture splits** with an anti-leakage
  guardrail: calibration and threshold tuning cannot read or tune against the
  holdout split — any attempt raises an error rather than silently leaking.
- **Validation gates scoped by VCEP, gene, disease, population, and variant class**,
  so a single benchmark can pass overall yet expose a specific failing scope.
- **Per-case reviewer review packets** carrying a machine-readable reviewer
  decision, accepted/rejected override proposal, and signature/sign-off metadata,
  plus a **serious-discordance adjudication workflow** that records each unresolved
  pathogenic-vs-benign conflict's root cause, proposed remediation, reviewer
  disposition, and release-blocking status — an unresolved serious discordance
  blocks release until a disposition is recorded.
- **Configurable conflict-policy checks** (for example, a BA1/BS1 benign-frequency
  signal colliding with curated pathogenic evidence), cleared only by a signed,
  per-variant exception rather than a global threshold change.
- **Locked regression baselines** pinning the current serious-discordance cases,
  raw-versus-enriched ClinVar deltas, and matched-versus-unmatched concordance, so
  any evidence change is reviewed intentionally instead of drifting silently.
- A continuous-integration pipeline exercising PostgreSQL-backed tests, migration
  apply/restore rehearsal, Docker image build, generated validation-report
  artifacts, headless frontend checks, and optional FHIR profile validation.
- Source-governance documentation for public-data versions, licenses, checksums,
  cache policy, and reproducibility, with a commit guard that blocks large or raw
  data files from entering version control.
- Clinical-policy documentation covering configuration review, release policy,
  conflict handling, and operational procedures.

---

## What ReClass Does Not Do

ReClass still does **not** provide:

- A credentialed clinical sign-off of the scoring configuration or PS4 rules. The
  governance review is complete; the credentialed human sign-off is not.
- A formal clinical validation study on an independent patient cohort, or any
  regulatory clearance (FDA SaMD, CLIA LDT validation, IVDR/UKCA).
- Data licensing confirmation for non-research / clinical use of ClinVar, ClinGen,
  REVEL, and gnomAD.
- A production-grade identity/deployment stack. The authentication, authorization,
  audit, observability, deployment, and reviewer-app pieces exist as
  **proof-of-concept service surfaces**, not a hardened, externally penetration-
  tested, single-sign-on-integrated production system.
- Independent assessment of papers, functional assays, segregation evidence,
  phenotype match, or expert assertions. It scores the structured evidence it is
  given; it does not judge whether that evidence is true.
- Broad automated evidence discovery for every variant class. Structured-input
  providers now exist for repeat-expansion, mitochondrial, non-coding,
  complex-indel, and richer structural-variant evidence, but the project still does
  not populate those signals from raw clinical/laboratory sources or validate them
  for autonomous clinical use.

---

## Productization Direction

The 2026-06-19 review reframed the scalable-product path — ReClass should be an
evidence-backed variant reclassification operations platform, not just a scoring
engine — and the code-actionable backlog for that reframe is now **built and
tested** (it is no longer a todo list). It was organized around five feature areas,
all delivered:

- **Evidence workbench and evidence operations** — done: reviewer-entered structured
  evidence for missing `PVS1`/`PS3`/`PM3`, `BA1`/`BS1`, phenotype, segregation,
  phasing, functional, and cohort evidence, with coverage dashboards and curation
  queues (see *Evidence workbench, coverage, and import* above).
- **Release-gate workflow hardening** — done: credentialed sign-off now enforces the
  release policy as a structured, five-state machine with validated sign-off packets.
- **Continuous reanalysis operations** — done: queues, run manifests, alert
  ownership/SLA/severity, amended-report tracking, notification workflow, and
  per-tenant reanalysis policies.
- **Enterprise deployment and security** — done: fail-closed production preflight,
  OIDC-only production auth, rate/request limits, audit retention, tenant
  administration, and SLO metrics.
- **Integration surfaces** — done: LIS/EHR amended-report adapter around the FHIR
  Genomics serializer, VCF/CSV/batch import, an OpenAPI-generated client, a webhook
  delivery subsystem, exportable validation packets, and tenant-onboarding readiness
  checks.

These features make the existing engine usable at scale, but they do not remove
the need for intended-use definition, clinical validation, data licensing,
regulatory strategy, and credentialed human accountability. The remaining work is
tracked in [`gap.md`](gap.md) and the staged pathway in [`roadmap.md`](roadmap.md).

---

## Input Model

ReClass can operate on benchmark records or future clinical/research records. A
record may contain direct ACMG criteria, source signals that can be converted into
criteria, or both. The reviewer always supplies (or confirms) the evidence; ReClass
arranges and scores it. In the operations service, evidence enters either case by
case through the **evidence workbench** (with reviewer, source-version, checksum,
access-date, and re-review provenance) or in bulk through the **batch / VCF / CSV
import** surfaces, which normalize identity and scrub patient identifiers before any
de-identified evidence is stored. The inputs below are what a record can carry,
regardless of which channel delivered it.

| Input | What It Means | How ReClass Uses It |
|---|---|---|
| Variant coordinates | Chromosome, position, reference allele, alternate allele, and genome build | Normalizes the variant, creates source/provider and storage keys, links evidence across sources, and stores de-identified variant evidence |
| ClinVar Variation ID / Allele ID | ClinVar source identifiers | Used as the highest-priority routes for direct ClinVar-to-ClinGen evidence matching when available |
| SPDI expression | NCBI SPDI variant notation | Parsed to a canonical locus as an additional identity-matching route |
| Transcript identity (MANE Select) | RefSeq/Ensembl transcript, gene, coding/protein HGVS | Carried into the evidence bundle and reports, and used as a transcript-level matching route (version-agnostic) |
| Variant loci / genomic HGVS | Assembly-explicit genomic coordinates parsed from ClinGen records | Builds canonical and HGVS-backed indexes so ClinGen evidence can match ClinVar variants even without a shared Variation ID |
| Gene/disease/VCEP context | Gene symbol, disease context, expert-panel context, or variant key | Selects reviewable VCEP/gene/disease configuration overrides when present |
| Structured ACMG/AMP criteria | Examples: PVS1, PS3, PM2, PM3, PP1, PP3, PP4, BA1, BS1, BP4, with direction and strength | Scored directly by the point model |
| REVEL score | Missense pathogenicity score for single-nucleotide missense variants | Converted to PP3 or BP4 according to calibrated bins; indeterminate scores are recorded without adding points |
| AlphaMissense score | Missense pathogenicity score for single-nucleotide missense variants | Converted to PP3/BP4 according to reviewable bins; when REVEL is also present, a single consensus computational event is emitted |
| Conservation score | phyloP-style conservation score | Converted to supporting PP3/BP4 only when configured thresholds are met |
| Gene constraint metrics | LOEUF, pLI, and regional missense Z | Recorded as context for mechanism/constraint review; not scored as an independent ACMG criterion |
| gnomAD frequency | Preferably `joint.faf95.popmax`, with genome/exome AF fallback | Converted to BA1, BS1, or PM2-style frequency evidence when thresholds are met |
| ClinGen Evidence Repository criteria | Expert-panel-applied ACMG criteria tied to source IDs or loci | Added as structured criteria when a case matches ClinGen evidence |
| Loss-of-function annotation | Predicted LoF consequence, NMD-escape status, gene LoF mechanism | Converted to PVS1 at a strength consistent with ClinGen PVS1 decision logic |
| Functional-assay result | A validated assay outcome with an OddsPath value | Converted to PS3 or BS3 at a calibrated strength |
| In-trans / phasing observation | Pathogenic-in-trans points for a recessive context | Converted to PM3 using a point-based scheme |
| Segregation data | Count of informative meioses | Converted to PP1 (or BS4 for non-segregation) |
| Phenotype specificity | A specificity grade for phenotype match | Converted to PP4 |
| Splice prediction | A SpliceAI-style delta score and/or canonical-site position | Converted to PP3/BP4, or PVS1 at canonical ±1,2 sites |
| Copy-number / dosage call | A dosage category for a CNV | Converted to PVS1 or PM4 |
| Non-coding / regulatory signal | A structured category such as established promoter effect, predicted splice effect, or no predicted effect | Converted to PM1/PM4/PP3/BP4/BP7 when configured categories apply |
| Complex indel signal | Frame and mechanism context for a multi-base or delins allele | Converted to PVS1 for LoF frameshift contexts or PM4 for qualifying in-frame changes |
| Mitochondrial signal | mtDNA frequency, heteroplasmy, and segregation-style context | Converted to BA1/BS1/PM2/PS4 under mtDNA-specific thresholds |
| Repeat-expansion signal | Repeat count and locus category for a known expansion locus | Converted to PVS1-style expansion evidence when the configured disease threshold is met |
| Structural-variant signal | Breakpoint/dosage-sensitive gene context beyond simple CNV category | Converted to PVS1/PM4/BP4/BA1 when configured categories apply |
| Cohort counts | De-identified case/control counts, totals (the PS4 denominator), and an optional odds ratio with confidence interval | Can generate PS4 evidence when configured cohort thresholds are met; the cohort counts and denominator are retained in the evidence bundle even when no PS4 point is awarded |
| Provenance metadata | Source, version, query ID, source records, match method, warnings, and review status | Preserved for audit, reporting, validation, and reconstruction |

Large genome reference files are not bundled. A local GRCh38 FASTA can be supplied
for reference-backed normalization; one is currently installed in this environment.

---

## Supported Evidence Sources

| Source | Current Use | Important Behavior |
|---|---|---|
| ClinGen Evidence Repository | Transfers expert-panel-applied ACMG criteria onto matching variants | Matching runs in priority order — ClinVar Variation ID, ClinVar Allele ID, canonical SNV key, SPDI, MANE/coding-HGVS transcript, then genomic HGVS; missing IDs, failed normalization, no match, ambiguous multi-record match (flagged, not resolved), and label disagreement are all reported |
| REVEL + AlphaMissense | Provide computational evidence for missense SNVs | High scores can contribute PP3; low scores can contribute BP4; when both are present, one consensus event is emitted rather than stacked predictor evidence |
| Conservation / gene constraint | Provide supporting computational/context signals | Conservation can contribute supporting PP3/BP4; gene constraint is context only and emits no independent points |
| gnomAD v4.1 | Provides allele-frequency evidence | Uses popmax FAF when available; falls back to genome/exome AF with warnings; absence from gnomAD is unknown evidence, not allele frequency zero |
| ClinVar | Provides public benchmark labels and some frequency fields | Used to measure how sparse public evidence behaves; labels are not treated as biological ground truth |
| Extended criteria providers | Provide reviewer-supplied LoF, functional, in-trans, segregation, phenotype, splice, CNV, non-coding, complex-indel, mitochondrial, repeat-expansion, and structural-variant evidence | Each maps a structured input to the appropriate ACMG/AMP criterion and strength using published calibration or reviewable local defaults; thresholds are versioned and reviewable |
| Upstream-evidence adapters | Provide reviewer/pipeline-supplied de novo, phasing, segregation, phenotype-specificity, functional-assay, disease-mechanism, and case-control evidence | Each maps the structured input to the matching ACMG/AMP criterion (PS2/PM6, PM3/BP2, PP1/BS4, PP4, PS3/BS3, PP2/BP1, PS4), records source version/checksum/access date, and emits an explicit no-call when the input is absent or malformed |
| De-identified cohort counts | Provide PS4-style enrichment evidence | Hearing Loss proband-count rules and Cardiomyopathy OR/CI rules are supported for encoded genes; PM2 evidence is supplied separately where required |

---

## How ReClass Uses Inputs

1. **Variant identity is normalized.** ReClass maps source-specific coordinates into
   a canonical identity format. Provider keys omit the build token; storage keys
   include it. Indels can be left-aligned against the reference genome when a local
   FASTA is present.
2. **Evidence is gathered.** Supplied criteria and evidence-provider results are
   assembled into an evidence bundle, with each provider's version and source
   records retained.
3. **Signals become criteria where possible.** REVEL, gnomAD, cohort-count, and the
   extended providers (LoF, functional, in-trans, segregation, phenotype, splice,
   CNV) can become ACMG/AMP-style criteria under configured, calibrated thresholds.
4. **Criteria are scored.** Each criterion contributes signed points according to
   its direction and strength.
5. **Points become a tier.** The net point total maps to the five-tier
   classification scale, with stand-alone benign rules (such as BA1) applied where
   configured.
6. **Provenance is attached.** Source versions, warnings, source records, match
   details, configuration version, and a reconstruction hash are included.
7. **Human review controls release.** Persisted classifications are drafts until a
   credentialed reviewer signs off.

The scoring core is deterministic. For the same evidence and same engine/config
version, it returns the same tier and the same reconstruction hash, with no
dependence on network access, randomness, or the wall clock.

---

## Scoring Model

ReClass uses a Tavtigian/ClinGen SVI-style point model.

| Evidence strength | Pathogenic points | Benign points |
|---|---:|---:|
| Supporting | +1 | -1 |
| Moderate | +2 | -2 |
| Strong | +4 | -4 |
| Very Strong | +8 | n/a |
| Stand-alone benign, such as BA1 | n/a | benign override |

| Net result | Tier |
|---|---|
| Pathogenic-level positive evidence | Pathogenic |
| Likely-pathogenic-level positive evidence | Likely Pathogenic |
| Neither sufficient pathogenic nor benign evidence | VUS |
| Likely-benign-level negative evidence | Likely Benign |
| Benign-level negative evidence or BA1 stand-alone | Benign |

The default configuration is reviewable and versioned. It is reconstructed from
published guidance and must be clinically reviewed and credentialed-signed before
use in patient care. As part of the governance review, the Hearing Loss GJB2
BA1/BS1 frequency thresholds were corrected to the current ClinGen Hearing Loss
CSpec, the PAH/PKU override was confirmed against the current PAH CSpec, and the
founder-frequency exception was reduced to an inert template pending a per-variant
signed review.

---

## The Reviewer Workflow and Application

ReClass models a clinician-in-the-loop workflow rather than autonomous reporting:

1. **Capture evidence** the public sources do not encode using the evidence
   workbench, and review the coverage and curation queues to see what is missing or
   ambiguous.
2. **Resolve evidence** for a variant from the configured providers.
3. **Classify** the assembled evidence to produce a tier and receipt.
4. **Persist a draft** classification, tied to a tenant.
5. **Review** the draft using the technical reviewer report.
6. **Clear the release gate and sign off** as a credentialed reviewer with a
   structured sign-off packet — only this step can move a draft toward a releasable
   state, and the gate blocks it if scope, preflight, conflict policy, or an
   unresolved serious discordance is not satisfied.
7. **Triage alerts** raised when reanalysis crosses a tier boundary, and track any
   amended report and clinician notification that results.

A browser-based reviewer application (served at `/reviewer/` when the service runs)
and a companion evidence-workbench page (`/reviewer/workbench.html`) walk a reviewer
through exactly this loop by calling the API. Authentication, role-based permissions
(viewer, reviewer, operator, administrator), and audit logging gate who can resolve,
classify, enter evidence, sign off, or change alert state. These are
proof-of-concept service surfaces intended to demonstrate the workflow, not a
validated production deployment.

---

## Outputs

For an individual classification, ReClass can produce:

- Predicted tier.
- Total points.
- Per-criterion contribution table.
- Evidence direction, strength, points, source, and source version.
- Stand-alone overrides, such as BA1.
- Provider versions and source records.
- Warnings and blocking normalization problems.
- Normalized/canonical identity.
- Engine/configuration version.
- Reconstruction hash.
- Draft or signed-off release status.

For human review, ReClass can produce:

- A **technical reviewer report** showing identity (including MANE transcript),
  evidence grouped by source, criteria, strengths, points, warnings, source
  records, PS4 cohort counts, population vs expert-panel (VCEP) group fields, prior
  classifications, reanalysis history, and alerts.
- A **patient-safe summary report** that avoids treatment or management directives.
- A **reviewer review packet** with a machine-readable reviewer decision,
  accepted/rejected override proposal, and signature/sign-off metadata, generated
  per serious-discordance case for adjudication.
- A deterministic **FHIR Genomics bundle** with draft/final/amended report state,
  suitable for replayable LIS/EHR transmission.

For operations and validation, ReClass can produce:

- Validation reports in Markdown and JSON.
- An **analytical-validation report** (Markdown and JSON) bundling engine/config
  version, source versions, confusion matrices, per-class recall, provider
  coverage, stratification tables, a reproducibility check, and the explicit
  not-signed-off clinical-release state.
- Failure-analysis reports, including a **per-case serious-discordance drill-down**
  that explains each high-risk error and separates engineering fixes from clinical
  sign-off decisions.
- Before/after comparison reports.
- Calibration reports by VCEP, gene, and disease group.
- An identity/normalization audit report (including a reference-backed re-run).
- Diagnostic plots.
- Reanalysis run reports showing checked, unchanged, same-tier changed,
  tier-crossing, failed, and skipped cases.
- Tier-crossing alerts and same-tier audit history.
- Audit-log entries for sign-off, alert state changes, reanalysis actions, and
  structured security events.
- Evidence-coverage summaries and roll-ups (by gene, VCEP, disease, variant class,
  and provider) and curation-queue items.
- VCF/CSV/batch **dry-run import reports** (normalized identity, duplicates, and an
  optional evidence-resolution preview) before anything is persisted.
- An exportable **validation/release packet** bundling config hash, source
  snapshots, benchmark metrics, serious-discordance disposition, and the sign-off
  ledger.
- Reanalysis operator views, per-tenant reanalysis policies, alert-triage records,
  amended-report and clinician-notification state, service-level-objective metrics,
  and signed outbound webhook deliveries.

---

## Current Validation Evidence

These results measure agreement with public reference labels. They are useful for
understanding reproducibility and evidence completeness, but they are **not** proof
of biological truth or clinical accuracy.

| Benchmark | Cases | Gate | Definitive Concordance | Serious Discordance | Overall Exact Concordance | Interpretation |
|---|---:|---|---:|---:|---:|---|
| `synthetic_v1` | 32 | PASS | 92.9% | 0 cases | 93.8% | Confirms scoring and harness behavior |
| `clingen_real_v1` | 12,446 | PASS | 94.7% | 4 cases | 93.0% | Expert-applied ClinGen criteria mostly reproduce expert-panel tiers |
| `clinvar_real_v1` | 21,638 | FAIL | 5.0% | 34 cases | 19.9% | Sparse public evidence is not enough for most ClinVar labels |
| `clinvar_enriched_v1` | 21,638 | FAIL | 42.4% | 6 cases | 46.6% | Adding matched ClinGen criteria substantially improves concordance but does not solve missing evidence |

The key scientific lesson is that the same scoring engine performs well when it
receives complete expert-applied evidence and poorly when the evidence is sparse.
The main blocker is evidence completeness and evidence quality, not threshold
loosening. The internal test suite (877 automated tests) is green in this
environment.

---

## ClinVar Enrichment Result

The current enriched ClinVar benchmark preserves the ClinVar expected labels but
adds ClinGen-applied ACMG criteria to matched cases.

| Measure | Count |
|---|---:|
| ClinVar cases | 21,638 |
| Direct ClinGen Variation ID matches | 10,649 |
| Canonical SNV-key fallback matches | 940 |
| Reference-backed indel-key fallback matches | 0 |
| Genomic HGVS fallback matches | 381 |
| Normalization failures | 2 |
| Unmatched ClinVar cases | 9,668 |
| Cases gaining criteria | 11,970 |
| Total criteria added | 37,873 |
| Cases with enrichment warnings | 9,700 |
| ClinVar/ClinGen label disagreements among matched records | 30 |
| Multiple ClinGen match cases resolved deterministically | 2 |

Compared with raw ClinVar, enrichment raised definitive concordance from 5.0% to
42.4%, raised overall exact concordance from 19.9% to 46.6%, and reduced serious
pathogenic/benign discordances from 34 to 6. Per-tier recall improved most for the
classes that depend on supplied expert evidence — Pathogenic recall rose from 0% to
32.1% and Likely Pathogenic recall from 0% to 55.9%.

Canonical SNV-key matching now contributes real-data lift because the ClinGen-
derived fixture exposes usable loci. Genomic HGVS matching adds further
reference-backed indel matches when the ClinGen record carries a GRCh38 genomic
HGVS token. Native reference-backed indel-key matching is implemented but yields 0
additional matches on the current real data, because the real ClinVar/ClinGen
indels in this fixture have no repeat-shifted spelling collisions for left-
alignment to resolve — a genuine empirical finding, not a missing capability. The
enriched fixture still fails the validation gate because many variants remain
unmatched or still lack the full evidence expert reviewers use.

---

## Privacy, Storage, And Reanalysis

The database model separates two domains:

- `clinical`: tenant-scoped, identified clinical data such as patients,
  classifications, sign-off/release-gate fields, alerts and their triage state,
  audit-log entries, reanalysis records and policies, evidence-coverage roll-ups,
  curation-queue items, amended-report and notification state, tenant
  administration, and webhook endpoints/deliveries.
- `research`: de-identified variant evidence, evidence bundles, source records,
  cohort counts, and reviewer-entered workbench evidence.

Research tables intentionally carry no patient or tenant identifiers and no foreign
key back to the clinical schema. Tenant isolation and the clinical/research
boundary are protected by database row-level security and covered by tests.

Stored classifications can be verified by replaying the persisted evidence under
the recorded engine/config version and comparing the resulting tier and
reconstruction hash. Evidence-bundle provenance can also be checked for tampering.

Reanalysis can recompute classifications when evidence, provider versions, or
configuration versions change. It avoids duplicate churn, records same-tier changes
as audit events, and creates clinical alerts only on tier crossings.

---

## Appropriate Use

This proof of concept is appropriate for:

- Auditing how a fixed ACMG/AMP-style rule set behaves on supplied criteria.
- Comparing evidence completeness across public sources.
- Studying which missing evidence categories drive disagreement.
- Reproducing benchmark runs in a controlled local environment.
- Testing provenance-preserving storage, reconstruction, and reanalysis.
- Prototyping and demonstrating a human-reviewed reclassification workflow,
  including the reviewer application and access-control surfaces.

It is not appropriate for:

- Autonomous patient diagnosis.
- Clinical reporting without local validation and credentialed sign-off.
- Treatment or management recommendations.
- Estimating penetrance, severity, or personal disease risk.
- Claiming that ClinVar or ClinGen reference labels are ground truth.
- Production clinical deployment on the current proof-of-concept security/operations
  surfaces.

---

## Main Limitations

ReClass re-sums supplied evidence. It does not independently judge the quality of a
paper, functional assay, segregation claim, phenotype match, case-control result,
or expert assertion.

Variant-type coverage is uneven. Automated and reviewer-assisted signals are
strongest for missense SNVs and for the criteria the extended providers cover
(LoF, functional, in-trans, segregation, phenotype, splice, CNV, non-coding,
complex indel, mitochondrial, repeat expansion, and structural variant signals).
Many of those providers still depend on structured upstream inputs and local
clinical validation rather than autonomous evidence discovery.

Frequency-based reasoning inherits representation limits from gnomAD and related
population resources. Absence from a population database is treated as unknown
evidence unless a configured rule explicitly supports a frequency criterion.

For a fuller boundary statement, read [`limitations.md`](limitations.md).

---

## Unfinished Work

The remaining todo list is in [`gap.md`](gap.md), and the staged clinical/
regulatory pathway is in [`roadmap.md`](roadmap.md). In short, the remaining work
is concentrated in credentialed clinical sign-off and validation, data licensing
for clinical use, production deployment and identity-provider hardening, live
LIS/EHR integration, and real-world evidence population/calibration for the
structured providers. The engineering scaffolding for the workflow, storage, API,
reviewer app, and operations is in place; what remains is largely clinical,
regulatory, legal, data-source, and infrastructure work rather than missing core
code.

---

## Where To Look Next

- [`limitations.md`](limitations.md) gives the clinical and scientific boundary
  statement.
- [`roadmap.md`](roadmap.md) gives the staged path from proof of concept toward
  clinical use, with the binding gates.
- [`research.md`](research.md) explains how the project relates to published
  ACMG/AMP tools.
- [`plan.md`](plan.md) is the setup and runbook.
- [`ReClass Model/README.md`](ReClass%20Model/README.md) is the technical
  repository map.
- [`gap.md`](gap.md) lists only unfinished todos.
