# ReClass — Standardized Variant Reclassification Engine

ReClass is a local **proof of concept** for a deterministic, auditable, and
reproducible ACMG/AMP variant reclassification engine, wrapped in a clinician-in-the-loop
operations service.

> **Decision support only.** ReClass is **not** FDA-cleared, CLIA-validated, or
> suitable for autonomous patient reporting. A qualified human reviewer remains
> responsible for evidence assessment, interpretation, and sign-off. See
> [limitations.md](limitations.md) and [roadmap.md](roadmap.md).

## What it does

ReClass takes structured variant evidence, applies a deterministic
Tavtigian/ClinGen SVI point model, and returns one of the standard five tiers —
**Pathogenic, Likely Pathogenic, VUS, Likely Benign, Benign**.

The output is not just a tier. Each result is an **auditable receipt** recording the
evidence used, points contributed by each criterion, source versions, warnings,
review status, engine/configuration version, and a SHA-256 **reconstruction hash**.
The central design goal is reproducibility: the same standardized evidence, under the
same engine/config version, produces the same classification and the same hash — with
no dependence on network access, randomness, or the wall clock.

The same engine runs four ways:

1. **As a calculator** — feed it structured evidence, read the tier and receipt.
2. **As a validation harness** — replay across thousands of benchmark variants and
   measure concordance against expert reference labels.
3. **As an operations service** — resolve evidence, classify, persist a draft, capture
   reviewer-entered evidence, generate reports, enforce a release-gate sign-off, and
   continuously re-flag variants when evidence changes, all through an API and a
   clinician-facing reviewer web app.
4. **As a CLI** — the `reclass` operator command classifies a variant, runs benchmarks,
   checks the reference cache, compares runs, and regenerates reports.

## Key capabilities

- **Deterministic scoring core** ([`engine/scoring.py`](ReClass%20Model/engine/scoring.py)) —
  pure, with no I/O, network, or wall-clock dependency; versioned, reviewable
  VCEP/gene/disease config overrides.
- **Canonical variant identity & normalization** — reference-free normalization plus
  reference-backed indel left-alignment against a local GRCh38 FASTA, with multiple
  identity-matching routes (ClinVar Variation/Allele ID, SPDI, canonical SNV key,
  MANE/coding-HGVS, genomic HGVS) and explicit ambiguity accounting.
- **Evidence providers** — ClinGen ERepo, REVEL, gnomAD, AlphaMissense, conservation,
  and gene constraint, plus an extended layer for PVS1, PS3/BS3, PM3, PP1/BS4, PP4,
  splice, CNV, non-coding, complex-indel, mitochondrial, repeat-expansion, and
  structural-variant evidence. Each bundle preserves provider versions, source records,
  warnings, and match details.
- **Operations service** — tenant-aware FastAPI layer, a clinician reviewer frontend
  (`/reviewer/`), a case worklist, an evidence workbench with coverage/curation queues,
  batch/VCF/CSV import, an enforced five-state release-gate sign-off, continuous
  reanalysis with tier-crossing alerts, and a deterministic FHIR Genomics export.
- **Platform & security** — fail-closed production preflight, OIDC (RS256/JWKS) auth,
  RBAC, audit logging, rate limiting, SLO metrics, signed webhook delivery, and tenant
  administration/onboarding. PostgreSQL storage separates identified `clinical.*` data
  from de-identified `research.*` evidence, enforced by row-level security.

## Validation

ReClass is validated on synthetic, ClinGen, raw ClinVar, and ClinVar-plus-ClinGen
benchmarks. The headline result is a **pre-registered, blinded held-out evaluation**: a
deterministic 30% holdout keyed on GRCh38 locus, hidden from calibration, with a
hash-pinned engine/config and frozen acceptance criteria gated in CI.

| Held-out benchmark | Holdout n | Definitive concordance (95% CI) | Serious discordance |
|---|---:|---|---:|
| `clingen_real_v1` (primary) | 3,635 | **95.4%** (94.5–96.1%) | 2 (0.1%) |
| `clinvar_real_v1` | 6,487 | 5.1% (4.5–5.7%) | 13 |
| `clinvar_enriched_v1` | 6,487 | 49.1% (47.8–50.5%) | 6 |

The primary hypothesis passes (Wilson lower bound 94.5% ≥ 85% bar; serious-discordance
upper bound 0.2% < 1%) and tracks the development split within ~1 pp — evidence the
thresholds are **not** overfit. The same engine moving from 5.1% (sparse ClinVar) to
49.1% (with matched ClinGen criteria) confirms that **evidence completeness — not the
scoring math — is the binding constraint**. This is analytical validation on public
benchmarks; it does not replace the independent clinical cohort study in
[roadmap.md](roadmap.md).

## Quick start

```bash
cd "ReClass Model"

../.venv/bin/python -m unittest discover -s tests -v   # 945 tests (914 pass, 31 skip without PostgreSQL)
../.venv/bin/python validation/harness.py              # synthetic gate
../.venv/bin/python validation/harness.py clingen_real_v1
../.venv/bin/python validation/holdout_eval.py         # pre-registered held-out gate
../.venv/bin/python -m engine.reference_cache --status
```

## Status

Latest local review **2026-06-23**: 945 tests ran successfully (914 passed, 31
PostgreSQL-backed storage/RLS tests skipped locally), with `ruff`, scoped `mypy`, the
reviewer frontend browser harness (80/80), validation baselines, and the held-out gate
all passing.

The engine, service scaffold, and a full scalable-product feature layer are **built and
tested**. The remaining work is **not** missing core code — it is clinical, data,
regulatory, and infrastructure hardening: credentialed clinical sign-off, a formal
clinical validation study, data licensing for clinical use, production
identity-provider rollout, live LIS/EHR integration, and real-world evidence population
for the structured providers.

## Documentation

| Doc | Audience |
|---|---|
| [overview.md](overview.md) | Practitioner / researcher overview |
| [ReClass Model/README.md](ReClass%20Model/README.md) | Technical repository map |
| [ReClass Model/manifest.md](ReClass%20Model/manifest.md) | Module status map |
| [ReClass Model/validation_report.md](ReClass%20Model/validation_report.md) | Validation summary |
| [limitations.md](limitations.md) | Clinical & scientific boundary statement |
| [roadmap.md](roadmap.md) | Staged path toward clinical use |
| [research.md](research.md) | Relationship to published ACMG/AMP tools |
| [plan.md](plan.md) | Setup and runbook |
| [gap.md](gap.md) | Unfinished todo list |
</content>
</invoke>
