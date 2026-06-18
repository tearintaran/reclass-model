# ReClass — Clinical Implementation Roadmap

Status: **Pre-clinical proof of concept.** Current clinical-release state is
`governance_reviewed_pending_credentialed_signoff` (see
[ReClass Model/docs/clinical_review.md](ReClass%20Model/docs/clinical_review.md)).
No output is patient-facing until the binding gates below are met.

This file describes the steps required to take ReClass from its current state into
clinical use. It is the *forward* plan; [gap.md](gap.md) remains the raw
unfinished-todo list and [overview.md](overview.md) is the project orientation.

> **This is an engineering/operations roadmap, not regulatory or clinical advice.**
> The pathway, validation design, and sign-off must be owned by a qualified
> regulatory affairs specialist and a credentialed clinical lab director.

---

## The core reframe

The remaining work is mostly **not** code. The engine, evidence providers,
persistence with row-level security, API (with a pinned OpenAPI contract and
startup preflight checks), sign-off workflow, audit logging, change-control
reanalysis triggers, FHIR export, deployment, observability, and CI are already
scaffolded and tested (781 tests passing). What separates this from a clinical
product is **clinical validation, regulatory clearance, data licensing, and
credentialed human accountability** — none of which are software.

Validation evidence already on record:

| Benchmark | Cases | Definitive concordance | Serious discordance | Meaning |
|---|---:|---:|---:|---|
| `clingen_real_v1` | 12,446 | 94.7% | 4 | Engine reproduces expert panel calls **when fed complete evidence** |
| `clinvar_real_v1` | 21,638 | 5.0% | 34 | Public data **alone** is too sparse for clinical use |
| `clinvar_enriched_v1` | 21,638 | 42.4% | 6 | Adding expert criteria helps; coverage is still the blocker |

The strategic conclusion: the engine logic is sound; **evidence completeness,
validation, and governance are the gating constraints.**

---

## Legend

- ⚠️ **Binding gate** — cannot ship to patients without it.
- 🔧 **Scaffolded** — exists in the repo; needs completion/hardening, not greenfield work.
- 🆕 **New build** — not yet started.
- Checkboxes track completion. Each phase lists **Owner**, **Depends on**, and **Exit criteria**.

---

## Phase summary

| # | Phase | Gate | Primary owner | Depends on |
|---|---|---|---|---|
| 0 | Intended use & regulatory pathway | ⚠️ | Regulatory + Lab Director | — |
| 1 | Clinical & scientific sign-off | ⚠️ | Lab Director / VCEP-qualified reviewer | 0 |
| 2 | Evidence & reference completeness | ⚠️ | Bioinformatics / Variant science | 0 |
| 3 | Data licensing & governance | ⚠️ | Legal + Data governance | 0 |
| 4 | Clinical validation study | ⚠️ | Clinical lab + Biostatistics | 1, 2, 3 |
| 5 | Software as a clinical product (QMS) | 🔧 | Software + Quality | 0 |
| 6 | Integration & operations | 🔧 | Software + Lab operations | 5 |
| 7 | Post-market surveillance | ⚠️ | Lab operations + Quality | 4, 6 |

Critical path to first patient use: **0 → 1 → 4** (with 2 and 3 feeding 4).
Phases 5–6 run in parallel and gate *deployment*, not scientific validity.

---

## Phase 0 — Intended use & regulatory pathway ⚠️

Everything downstream depends on this fork; do it first.

- [ ] Write the **Intended Use / indications-for-use statement**. Keep ReClass in
  the *clinician-in-the-loop decision-support* lane — the repo already enforces a
  `draft → credentialed sign-off` gate
  ([ReClass Model/docs/release_policy.md](ReClass%20Model/docs/release_policy.md)).
- [ ] Determine the **regulatory pathway** with qualified counsel:
  - [ ] **US — CLIA Laboratory-Developed Test (LDT)** run inside a CLIA-certified,
    CAP-accredited lab, with the lab director owning each result. Confirm current
    FDA LDT-oversight status.
  - [ ] **US — FDA SaMD / Clinical Decision Support** if distributed as software.
    Genetic interpretation CDS likely falls *outside* the non-device CDS carve-out
    (21st Century Cures §3060) → probably a regulated device (commonly Class II,
    510(k)).
  - [ ] **EU — IVDR** classification + CE marking; **UK — UKCA**.
- [ ] Decide the **business model** (in-house lab tool vs. distributed product) —
  this determines whether ISO 13485 / 510(k) / CE marking apply.
- [ ] Record the decision and rationale in a **regulatory strategy memo** under
  `ReClass Model/docs/`.

**Exit criteria:** signed intended-use statement + documented regulatory pathway.

---

## Phase 1 — Clinical & scientific sign-off ⚠️

These are the binding blockers in the clinical-review ledger and [gap.md](gap.md).

- [ ] **Credentialed sign-off of the scoring configuration**
  ([ReClass Model/engine/configs/base_v1.json](ReClass%20Model/engine/configs/base_v1.json),
  hash-pinned) by a lab director / ABMGG/CCMG-equivalent. The ledger currently
  records this as *pending*.
- [ ] **Verify every VCEP / gene / disease override** against the *current*
  published ClinGen CSpec. (Governance review already caught a wrong GJB2 threshold
  — this review matters.)
- [ ] **Verify the PS4 cohort/proband rules** in
  [ReClass Model/monitoring/reanalysis.py](ReClass%20Model/monitoring/reanalysis.py)
  against current specs and local lab policy.
- [ ] **Ratify the conflict policy** (e.g., BA1/BS1 frequency vs. curated
  pathogenic evidence for founder variants) in
  [ReClass Model/docs/conflict_handling.md](ReClass%20Model/docs/conflict_handling.md).
  The configurable checks now exist in code
  (`ReClass Model/validation/conflict_policy.py`), including signed per-variant
  exceptions that never mutate a global threshold; credentialed ratification of the
  policy itself remains.
- [ ] **Adopt the release policy as enforced SOP**
  ([ReClass Model/docs/release_policy.md](ReClass%20Model/docs/release_policy.md)),
  including the re-review triggers.
- [ ] Define the **validated scope**: explicit in/out lists of genes, diseases,
  inheritance modes, and variant classes.

**Exit criteria:** signed review ledger entry recording reviewer, credential,
config hash, commit, source/spec versions, scope, and expiry date.

---

## Phase 2 — Evidence & reference completeness ⚠️

The validation table proves the engine needs *complete* evidence to be useful.

- [x] **Install a local GRCh38 FASTA cache** for reference-backed indel
  normalization in this environment; not bundled and still must be installed,
  checksum-pinned, and validated per production site. See
  [ReClass Model/data/reference/](ReClass%20Model/data/reference/).
- [x] **Re-run identity audits** with the FASTA present; current artifacts record
  SNV/indel duplicate and mismatch rates.
- [x] **Adopt standardized nomenclature** in code: HGVS + MANE Select transcripts
  and assembly-explicit identity now flow through the identity-matching routes and
  the evidence model. Confirming the nomenclature policy for the Phase 1 clinical
  scope remains a clinical-governance step.
- [x] **Broaden structured evidence beyond the ClinGen/REVEL/gnomAD slice.** The
  repository now has offline-tested structured-input providers for PVS1/PM4,
  PS3/BS3, PM3, PP1/BS4, PP4, splice, CNV, repeat expansions, mitochondrial
  variants, non-coding variants, complex indels, and richer structural-variant
  signals.
- [ ] **Populate those structured providers from validated upstream sources** and
  confirm thresholds/workflows for the Phase 1 clinical scope. This remains the
  largest scientific/data-source work item.
- [ ] **Separate population/ancestry fields from VCEP panel-group fields** in
  fixtures and reports. Validation gates can now be scoped by population as a
  distinct dimension (`ReClass Model/validation/analytical_validation.py`), but the
  current public fixtures still do not support equity or ancestry-performance
  claims.

**Exit criteria:** evidence coverage sufficient for the Phase 1 validated scope,
with reference-backed normalization audited.

---

## Phase 3 — Data licensing & governance ⚠️

- [ ] **Re-review license terms** of ClinVar, ClinGen, REVEL, and gnomAD for
  *clinical / non-research* use (REVEL and gnomAD terms differ from research use).
  This is a legal gate, not engineering.
- [ ] **Pin and refresh source snapshots** — record exact versions, checksums,
  access dates, and regeneration commands for every fixture under
  [ReClass Model/docs/data_governance.md](ReClass%20Model/docs/data_governance.md).
- [x] **Wire `ops/repo_guard.py` as a pre-commit hook** to enforce commit hygiene.

**Exit criteria:** written confirmation that every data source is licensed for the
intended clinical use, with versioned, reproducible snapshots.

---

## Phase 4 — Clinical validation study ⚠️

A formal study, not the development harness.

- [ ] **Analytical validation** — confirm the engine computes correctly (largely
  covered by the 781-test suite + synthetic/ClinGen gates). The report is now
  generated from a single command (`validation/analytical_validation.py` →
  `validation/reports/analytical_validation.md`/`.json`, also via
  `reclass report analytical-validation`); the remaining gate is a credentialed
  reviewer signing it off as a formal study artifact.
- [ ] **Clinical validation** on an *independent, representative* cohort with
  **pre-registered acceptance criteria**:
  - [ ] concordance vs. expert truth within scope
  - [ ] sensitivity / specificity
  - [ ] **near-zero tolerance for serious discordance** (P↔B flips)
- [ ] **Reproducibility check** — confirm identical engine version + config hash
  reproduces identical classification + reconstruction hash across runs.
- [ ] **Sign off the validation report** under
  [ReClass Model/docs/release_review.md](ReClass%20Model/docs/release_review.md).

**Exit criteria:** signed validation study meeting pre-set acceptance criteria with
no unresolved serious discordance in scope.

---

## Phase 5 — Software as a clinical product (QMS & lifecycle) 🔧

Mostly scaffolded; needs formalization.

- [ ] **Quality Management System**: ISO 13485 (if SaMD), risk management
  ISO 14971, software lifecycle IEC 62304, design controls + Design History File.
- [ ] **Security & privacy**:
  - [ ] HIPAA technical safeguards + Business Associate Agreements
  - [ ] encryption at rest and in transit
  - [ ] third-party **penetration test**
  - 🔧 Harden existing auth (`ReClass Model/api/auth.py`, `api/oidc.py`),
    authorization (`authz.py`), audit log + retention (`audit.py`,
    `ReClass Model/deploy/migrations/001_audit_log.sql`), tenant RLS, and
    observability (`observability.py`, `/metrics`). RS256/JWKS validation exists;
    production identity-provider rollout, key-management policy, and security
    review remain.
- [ ] **Human-factors / usability validation** of the reviewer UI
  (🔧 `ReClass Model/frontend/`, mounted at `/reviewer/`).
- [ ] **Deployment & resilience**: 🔧 Docker/Compose, migration ledger, backup
  script, restore script, local restore rehearsal, and health/metrics endpoints
  exist — add HA, TLS/reverse proxy, production monitoring/alerting, and a
  site-specific **restore / disaster-recovery** procedure. See
  [ReClass Model/docs/deployment.md](ReClass%20Model/docs/deployment.md).

**Exit criteria:** QMS in place, pen-test remediated, DR restore tested.

---

## Phase 6 — Integration & operations 🔧

- [ ] **LIS / EHR integration** via HL7 / FHIR Genomics, with a defined
  result-transmission and amended-report workflow. A deterministic FHIR Genomics
  serializer with draft → final → amended state transitions and byte-identical
  replayable outbound payloads exists in `ReClass Model/reporting/fhir.py`; the
  live connection to a real LIS/EHR remains.
- [ ] **Operational SOPs** for reanalysis runs, alert triage, sign-off, and
  patient-safe summary release (start from
  [ReClass Model/docs/operations_sop.md](ReClass%20Model/docs/operations_sop.md)).
- [ ] **Change control & versioning** — the engine's version-pinned config and
  reconstruction hashes are now wired to automated change-control triggers
  (`ReClass Model/ops/scheduler.py`): a source-snapshot, provider-version, config,
  or conflict-policy change enqueues the affected variants with an auditable run
  manifest. Adopting this as the enforced re-review SOP in the release policy
  remains.

**Exit criteria:** end-to-end order → classify → review → sign-off → release →
amend workflow operational and documented.

---

## Phase 7 — Post-market surveillance ⚠️

- [ ] **Reanalysis at scale** (the system's differentiator — already built) on a
  documented periodicity.
- [ ] **Discordance & incident tracking**, complaint handling, and a process to
  push reclassifications back to ordering clinicians.
- [ ] **Periodic re-review** of config, sources, and validation as specs evolve.

**Exit criteria:** continuous surveillance and reclassification-notification loop
in routine operation.

---

## Shortest honest path to first clinical use

If the goal is real clinical use *soon*, the lowest-friction route is an
**LDT / decision-support tool inside an existing CLIA-certified, CAP-accredited
lab**, scoped to a **narrow set of genes/diseases** where Phases 1–4 can be
completed well. That collapses the work to:

1. Credentialed sign-off on the config (Phase 1)
2. License the data for clinical use (Phase 3)
3. Complete a scoped validation study with pre-set criteria (Phase 4)
4. Operate under the lab's existing QMS (Phase 5, reused)

Broad evidence coverage (rest of Phase 2) and a standalone regulated product
(full Phase 5 + SaMD pathway) come later.

---

## Non-negotiable minimum before any patient sees output

1. A credentialed reviewer **signs the config** (Phase 1).
2. The data is **licensed for clinical use** (Phase 3).
3. A **scoped validation study passes** pre-set criteria (Phase 4).
4. Every result clears the **`release_policy.md` gates with human sign-off**.

---

## Roles required

| Role | Responsible for |
|---|---|
| Regulatory affairs specialist | Phase 0 pathway, FDA/IVDR strategy |
| Clinical lab director (ABMGG/CCMG-equiv.) | Phases 1, 4 sign-off; result accountability |
| Variant-science / bioinformatics lead | Phase 2 evidence & reference work |
| Biostatistician | Phase 4 study design & acceptance criteria |
| Legal / data governance | Phase 3 licensing |
| Software / quality engineering | Phases 5–6 QMS, security, integration |
| Lab operations | Phases 6–7 SOPs and surveillance |
