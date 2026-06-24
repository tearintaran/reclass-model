# Research Context - Literature Review and Project Contributions

*Literature adjacent to the ReClass proof of concept, and the research extension
this project has actually accomplished.*

Last literature review: **2026-06-19** (prior pass 2026-06-17). This pass
re-verified every code-grounded claim below against the current repository
(`engine/scoring.py`, `evidence/model.py`, `storage/verify.py`, `db/schema.sql`,
`reporting/fhir.py`, and the validation/comparison/failure reports *regenerated
2026-06-19*) and re-checked the headline numbers (`synthetic_v1` 92.9% / 0 serious;
`clingen_real_v1` 94.7% / 4 serious; `clinvar_real_v1` 5.0% / 34 serious;
`clinvar_enriched_v1` 42.4% / 6 serious; the 5.0%→42.4% enrichment lift and its
confusion-matrix deltas; the named GJB2/SLC26A4/HNF4A and BRCA1 serious-error
cases), all of which **still hold** after the 2026-06-19 report regeneration. The
2026-06-17 pass had already corrected several inherited citation errors (Genome
Alert! is *Genetics in Medicine*, not *Genome Medicine*; the PP1/BS4/PP4 guidance is
Biesecker et al. 2024; AutoPVS1's author list; vcf2fhir is 2021; the ClinGen-vs-VCI
paper identity; single-lab vs multi-lab framing of the 2024 reclassification study).

The **2026-06-19 pass adds five mid-2026 developments** the field has moved on, each
verified against primary sources:

1. **The successor to the 2015 guideline now has a name, a method, and a pilot.** The
   long-anticipated revision is now the joint **ACMG/AMP/CAP/ClinGen "SVC v4.0"**
   standard — explicitly a **Bayesian, points-based** system with VUS subdivision and
   evidence-code overhaul, in final piloting (Biesecker et al., ACMG 2026 abstract
   P593) but **still unpublished** as of mid-2026. This is the single most important
   update because the field's *official* next standard is converging on the exact
   Tavtigian points model ReClass already implements (see §2.1, §6).
2. **GA4GH VRS 2.0 is formally released** (2.0.0/2.0.1, March 2024; 2.1 snapshots
   Feb 2026), with structural-variation support, compact ambiguous representation,
   metadata integration, and extensibility — sharpening the digest-identity parallel
   (§2.5, ref 41).
3. **gnomAD local-ancestry-inference** (Kore et al., *Nat Commun* 2025) shows that
   ancestry-aggregated frequencies mask ≥2-fold differences for the majority of
   variants in admixed groups — direct empirical support for open question 7 (§2.2,
   ref 14b).
4. **The 2025-2026 LLM-classifier picture is now more nuanced**: recent models show
   *high self-consistency* (>90% same answer on repeat) yet still hallucinate and
   misclassify pathogenicity, and "precision grounding" in evidence databases is the
   emerging mitigation — which refines, rather than overturns, the case for a
   *reconstructable* engine (§2.3, ref 25).
5. The HL7 **FHIR Genomics Reporting IG v4.0.0** is now in active continuous build
   (2026-01-30); v3.0.0 (STU3) remains the current *published* version the serializer
   targets (§2.5, ref 42).

The three earlier-added threads remain: the ClinGen SVI splicing recommendations the
engine's splice mapper targets, and the canonical FAIR principles anchor (Wilkinson
2016) behind the provenance framing.

This review positions the **Standardized Variant Reclassification Engine**
(`ReClass Model/`) against published ACMG/AMP variant-interpretation literature,
current ClinGen guidance, and adjacent automated-classification tools. It is
deliberately conservative: ReClass does **not** claim new biology, new ACMG/AMP
combining rules, or a new PP3/BP4 calibration. The contribution is best framed as
research engineering and experimental methodology around deterministic scoring,
evidence provenance, evidence-gap attribution, cryptographic reconstruction,
auditable reanalysis, and clinical/research data governance.

The project evidence used here comes from:

- `ReClass Model/engine/scoring.py` (pure scoring core + reconstruction hash)
- `ReClass Model/evidence/model.py` (provenance-rich `EvidenceBundle`)
- `ReClass Model/storage/verify.py` (receipt + bundle-provenance verifier)
- `ReClass Model/db/schema.sql` (two schemas, six RLS policies)
- `ReClass Model/monitoring/` and `ReClass Model/ops/` (reanalysis, diff, alerts)
- `ReClass Model/reporting/fhir.py` (deterministic FHIR Genomics export)
- `ReClass Model/validation/reports/`
- `overview.md`, `limitations.md`, and `gap.md`

External sources are listed in the final section, each with a verified PMID/DOI or
canonical URL.

---

## 1. One-paragraph project summary

ReClass is a deterministic, auditable ACMG/AMP-style variant-classification
engine. It maps structured evidence events, selected source signals
(ClinGen-applied criteria, REVEL/AlphaMissense/conservation, gnomAD frequency,
gene-constraint context, extended structured criteria, and cohort-count PS4
helpers), and versioned configuration into a signed point total using the ClinGen
SVI / Tavtigian Bayesian point framework. It returns the five-tier result
(Pathogenic, Likely Pathogenic, VUS, Likely Benign, Benign), per-criterion
contributions, stand-alone overrides such as BA1, provider/source provenance,
engine/config version, and a SHA-256 reconstruction hash. The repository also
implements evidence bundles, ClinGen/REVEL/gnomAD/AlphaMissense/computational and
extended structured-evidence providers, real-data validation fixtures, failure
analysis, ClinVar enrichment from ClinGen matches, PostgreSQL storage with
row-level tenant isolation, clinical/research data separation, reanalysis queueing,
tier-crossing alerting, reviewer reports, patient-safe summaries, deterministic
FHIR Genomics export, and credentialed human sign-off surfaces.

Validation in this snapshot:

| Benchmark | Cases | Gate | Definitive concordance | Serious discordance | Overall exact concordance |
|---|---:|---|---:|---:|---:|
| `synthetic_v1` | 32 | PASS | 92.9% | 0 | 93.8% |
| `clingen_real_v1` | 12,446 | PASS | 94.7% | 4 | 93.0% |
| `clinvar_real_v1` | 21,638 | FAIL | 5.0% | 34 | 19.9% |
| `clinvar_enriched_v1` | 21,638 | FAIL | 42.4% | 6 | 46.6% |

These are concordance-with-reference numbers, not proof of biological truth or
clinical readiness.

---

## 2. Literature landscape adjacent to ReClass

### 2.1 Foundational ACMG/AMP and quantitative frameworks

The direct foundation is the ACMG/AMP 2015 framework from Richards et al. [1],
which defines five pathogenicity categories and 28 qualitative evidence criteria.
This is the vocabulary ReClass uses.

Tavtigian et al. 2018 [2] showed that the qualitative ACMG/AMP combining rules are
largely compatible with a Bayesian framework. Tavtigian et al. 2020 [3] then fitted
a naturally scaled point system to the guideline categories: supporting = 1,
moderate = 2, strong = 4, very strong = 8 (benign criteria negative), with
pathogenic/benign point totals mapped to classification tiers. ReClass adopts this
model rather than inventing a new one (`engine/config.py`,
`engine/configs/base_v1.json`).

**Freshness check (mid-2026).** There is still **no published replacement** for the
Richards et al. 2015 guideline — but the replacement is now concretely named and in
final piloting. The long-anticipated revision has become the joint
**ACMG/AMP/CAP/ClinGen Sequence Variant Classification standard, "SVC v4.0"**
(co-chaired by Leslie Biesecker and Steven Harrison), and its design is now public
through a March 2026 ACMG-meeting pilot report (Biesecker et al., abstract P593 [7]):
it is explicitly a **Bayesian, points-based system with flow diagrams** that walk a
curator through evidence application, it **subdivides the VUS tier by likelihood of
pathogenicity** onto a graded scale, and it carries a **complete overhaul of the
evidence-code labels** to be more concept-driven. The validity/usability pilot
reported **85% (17/20) of test variants reaching >90% concordance** on the
three-level scale, and is expanding to 30 variants and >100 community curators before
release. Crucially, SVC v4.0 **is not yet a published guideline** as of June 2026 —
ACMG's "Documents in Development" list still carries it as forthcoming ("will soon be
released") [7] — so it must not be cited as a published standard. The active citable
frontier remains: (1) the endorsed Tavtigian/ClinGen-SVI point system, increasingly
the default combining method and now reused outside germline work — the
ClinGen/CGC/VICC somatic *oncogenicity* standard [4] adopts the same +1/+2/+4/+8
point shorthand, and SVC v4.0 makes the same Bayesian points model the *official*
germline standard; (2) gene/disease-specific VCEP criteria specifications (CSpecs);
and (3) ongoing computational-predictor calibration. The current citable surface for
the combining rules is the ClinGen Variant Classification Guidance page (last updated
July 2025) [5], which now aggregates and supersedes the standalone SVI recommendation
pages; the SVI Working Group page itself was **retired in April 2025** [6].

**Why SVC v4.0 matters for ReClass (and is favorable to it).** Two of SVC v4.0's
headline changes are properties ReClass already has, which moves the project *with*
the standard rather than against it. First, the new official standard adopts the
Bayesian points model ReClass implements (`engine/config.py`,
`engine/configs/base_v1.json`) — vindicating the choice to score on Tavtigian points
rather than the qualitative 2015 combining rules. Second, the evidence-code overhaul
is exactly the kind of change ReClass's architecture is built to absorb: because the
governed *point core* is separated from the reviewable evidence-code → point mappings
in versioned config, a future re-label of criteria can be applied as a new config
version with a new engine fingerprint, leaving every historical receipt
reconstructable under its recorded version. The one genuinely new demand SVC v4.0
places on the project is its graded VUS sub-tier: ReClass currently collapses
insufficient-evidence cases to a single VUS (its dominant failure mode on sparse
data, §4.2), whereas SVC v4.0 will expect a likelihood-graded VUS — a concrete,
bounded extension target rather than a redesign (see open question 10).

The VCEP track is moving fastest and is directly relevant to ReClass's override
mechanism. Recent examples include the ClinGen RASopathy VCEP update (Wilcox et al.,
*Genetics in Medicine Open*, 2025) [30], which refined recessive-criterion handling
and re-tuned PP3/BP4 cutoffs; the PALB2 VCEP 2025 specification [31]; and the ENIGMA
BRCA1/BRCA2 VCEP gene-specific specifications [32]. ReClass's reviewable
VCEP/gene/disease overrides (`engine/configs/base_v1.json`) are the engine-side
analogue of exactly this work, which is why they are versioned and gated on
credentialed sign-off rather than hard-coded.

**Implication for ReClass.** The project is not novel because it scores ACMG/AMP
evidence. Its novelty must be judged downstream of the score model: deterministic
reconstruction, evidence provenance, validation design, auditable reanalysis, and
operational governance.

### 2.2 Criterion-specific calibration and evidence-source literature

Pejaver et al. 2022 [8] calibrated computational predictors for ACMG/AMP PP3/BP4
evidence; the calibrated REVEL intervals reach supporting, moderate, or strong
evidence depending on the score band (PP3 supporting ≥0.644, moderate ≥0.773,
strong ≥0.932; BP4 mirrored downward). ReClass implements REVEL PP3/BP4 bins from
this literature (`engine/config.py`, `engine/scoring._revel_to_event`). Bergquist,
Stenton, Nadeau et al. 2025 [9] extended ClinGen computational calibration to
AlphaMissense, ESM1b, and VARITY, and reported a clinically important nuance: the
tool-developer-recommended AlphaMissense threshold (0.564) and ESM1b threshold
(−7.5) do **not** themselves meet even the Supporting evidence level under formal
calibration — the calibrated thresholds are markedly more stringent (e.g.
calibrated AlphaMissense Supporting-pathogenic ≈ [0.100, 0.169]). ReClass's
AlphaMissense bins live in a separate `computational_ext_v1.json` precisely so
these calibrated cut-points can be revised without touching the governed base point
model, and `resolve_missense_consensus` combines REVEL + AlphaMissense into a
*single* PP3/BP4 event (ACMG does not stack predictors).

Brnich et al. 2019 [10] provided structured recommendations for PS3/BS3 functional
evidence (OddsPath-based), which ReClass's `_functional_to_event` mapper follows
(2025 ClinGen SVI consultation work continues to refine functional-evidence use).
ClinGen also provides PP1/BS4 co-segregation and PP4 phenotype-specificity guidance
(Biesecker et al. 2024 [11]) and SVI splicing recommendations (Walker et al. 2023
[12]) with SpliceAI delta-score thresholds — directly relevant because ReClass's
`_splice_to_event` maps SpliceAI-style deltas to PP3/BP4 and routes canonical-site
variants to PVS1. These matter because many variants in real practice are
classified from functional, segregation, proband, phenotype, splice, or
mechanism-specific evidence — exactly the criteria ReClass's extended providers
*accept* but do not autonomously *derive* from raw biology.

REVEL [13] and gnomAD [14] are adjacent data sources rather than classification
engines. gnomAD v4.1 (April 2024) [14] added joint exome+genome allele-number
reporting and flags for discordant exome/genome frequencies, underscoring why
frequency evidence needs source/version provenance and cannot be treated as
timeless — which is why ReClass records `joint.faf95.popmax` provenance and treats
database absence as *unknown*, not as allele-frequency zero. (gnomAD v5 is
anticipated but **still not released as of mid-2026**; v4.1 remains the current
reference.) A 2025 advance sharpens the equity caveat directly: Kore, Wilson, Tiao
et al. applied **local ancestry inference** to >27M variants in gnomAD's admixed
Admixed-American and African/African-American groups and found that **78.5% and 85.1%
of variants, respectively, show ≥2-fold differences in ancestry-specific
frequencies** that the standard aggregated estimate masks — frequencies granular
enough to flip some BA1/BS1/PM2 calls (the authors note it can reclassify VUS toward
benign) [14b]. This is direct empirical evidence for why ReClass treats frequency
evidence as a versioned, provenance-bearing signal rather than a single timeless
number, and motivates open question 7. ClinVar and the ClinGen Evidence Repository
[15] are the assertion sources the benchmarks draw on.

**Implication for ReClass.** ReClass's evidence coverage is broader than the first
public-source slice (it adds AlphaMissense, conservation, gene-constraint context,
extended structured criteria including splice, and configured cohort counts), but
it is narrow in a different way: most newer providers need validated structured
inputs and clinical review, not just code. The literature points toward *calibrated*
providers and *governed* source population, not toward loosening point thresholds.

### 2.3 Automated and semi-automated ACMG classifiers

Several tools already automate parts of ACMG/AMP interpretation:

| Tool or platform | Relevant literature/status | Relationship to ReClass |
|---|---|---|
| InterVar / wInterVar | Li and Wang 2017 [16]; widely used open-source implementation | Direct neighbor; auto-applies many criteria and expects human adjustment. |
| CharGer | Scott et al. 2019 [17] | Open-source germline cancer-focused classifier with custom modules and flexible scoring. |
| TAPES | Xavier et al. 2019 [18] | Implements ACMG assignment plus Tavtigian-style probability and cohort enrichment. |
| GeneBe | Stawiński and Płoski 2024 [19] | Web/API ACMG implementation with editable criteria; reports r≈0.90 vs ClinGen eRepo. |
| BIAS-2015 v2.1.1 | Eisenhart et al. 2025, *Genome Medicine* [20] | Open-source 19-criterion ACMG automation benchmarked against the FDA-recognized ClinGen eRepo; reports higher pathogenic sensitivity (73.99% vs InterVar 64.31%) and benign sensitivity (80.23% vs 53.91%), an ~11x speed-up (~1,327 variants/s), user-defined weighting, and transparent rationales. |
| AutoPVS1 | Xiang et al. 2020 [21] | Focuses on rigorous PVS1 strength automation rather than whole-classification provenance. |
| VarSome, Franklin, ELLA | Commercial/freemium or hosted platforms [22] | Clinically used; per-rule transparency, but not always independently reproducible or fully auditable from source. |
| OpenCRAVAT calibrated package | 2025 package built around ClinGen PP3/BP4 calibration [23] | Adjacent evidence-calibration infrastructure rather than a complete receipt system. |
| AutoPM3 | Li et al. 2025 [24] | LLM-driven extraction of PM3 (in-trans) evidence from the literature; a modular, criterion-specific assist. |
| LLM ACMG classifiers | DeepSeek/GPT-4o/Llama/Qwen frameworks 2025-2026 [25] | Emerging paradigm: often self-consistent yet still hallucinates/misclassifies and emits no replayable receipt — the explicit contrast ReClass's *reconstructable* design is built against. |

Two recent comparative studies frame this crowded field:

- **Ghasemnejad et al. 2026, *Bioinformatics*** [26] screened 537 studies, selected
  four ACMG/AMP tools (Franklin, InterVar, TAPES, GeneBe) from a field of 22, and
  benchmarked them against LIRICAL on 151 expert-curated Mendelian-disorder
  datasets. It found phenotype-aware approaches (Franklin, LIRICAL) outperform tools
  relying mainly on genomic features for variant *prioritization* (LIRICAL 68.21%
  vs Franklin 61.59% top-10) — directly relevant because ReClass intentionally does
  not automate phenotype fit (open question 5).
- **Costa et al. 2025, *Briefings in Bioinformatics*** [27] is a comprehensive
  review of automated variant interpretation that benchmarks tools against ClinGen
  Expert Panel calls on 256 variants (cardiomyopathies, hereditary cancer,
  monogenic diabetes) and catalogues the recurring "pitfalls": opaque criterion
  application, inconsistent automation rates (often <50–82% of criteria),
  systematic VUS-over-pathogenic bias, source-version drift, and limited
  auditability. This is the strongest single statement of the problems ReClass's
  receipt/provenance design is built to address.

**Implication for ReClass.** The adjacent literature is crowded on automated
criteria assignment and tool benchmarking, and the 2025-2026 frontier is adding
LLM-based assistants. The accurate framing for these is more nuanced than "they
drift": recent benchmarks of GPT-4o/Llama-3.1/Qwen-2.5 for variant classification
report *high self-consistency* (the majority of repeated queries return the same
answer >90% of the time) yet persistent **hallucination and outright pathogenicity
misclassification** — GPT-4o led at ~0.73 accuracy, a three-model consensus reached
~0.97 but only on the 26% of variants where all three already agreed, and O1 / Claude
3.5 Sonnet / DeepSeek-R1 each occasionally mis-call pathogenicity [25]. The emerging
mitigation is **"precision grounding"** — feeding LLMs curated evidence-database
annotations (ClinVar, ACMG codes) eliminates much of the hallucination [25]. So the
distinction ReClass should claim is not "deterministic vs random," but
**reconstructable vs unreplayable**: even a self-consistent LLM cannot emit a
version-bound receipt that a third party can re-derive byte-for-byte from the recorded
evidence and engine identity, and its residual misclassifications cannot be audited
to a fixed rule. ReClass should not claim to be the first automated classifier; its
stronger distinction is that it treats every classification as a reconstructable,
versioned, provenance-carrying receipt and uses *matched* validation fixtures to
isolate evidence completeness from scoring logic — the exact auditability gap Costa et
al. [27] call out. The "precision grounding" result also points at the productive
boundary (open question 9): an LLM is best used to *extract grounded evidence events*
that enter the receipt as signed inputs, not to be the classifier whose output is the
receipt.

### 2.4 Variant reanalysis and reclassification literature

ReClass ships a reanalysis/monitoring subsystem (`monitoring/diff.py`,
`monitoring/reanalysis.py`, `ops/queue.py`, `ops/scheduler.py`,
`ops/run_report.py`, `storage/alerts.py`); this is the literature it sits against.

Periodic reanalysis is a well-documented source of clinical value. Re-evaluation of
research exomes five years after the initial report revealed clinically relevant
changes in **18%** of families (28/152) — reclassifications, gene-disease-validity
changes, and new diagnoses (Bartolomaeus et al. 2023 [33]). A large single-laboratory
analysis quantified how reclassification evolves over time, including a roughly
**four-fold decrease** in the normalized rate of reclassification *to* VUS between
2015 and 2023 (0.0150 → 0.0038) as evidence and guidance matured, with P/B
classifications >99% stable (Kobayashi et al. 2024 [34]). Complementary work shows
that reanalysis **efficiency** improves dramatically when systems track updates in
clinical knowledge bases rather than blindly recomputing everything (Li et al. 2024
[35]: 241 candidates flagged from 3.8M variants by tracking deltas, ~18% of those
reclassified); that VUS reclassification rates differ sharply once a VUS is
sub-stratified by evidence level (Bennett et al. 2025 [36]: VUS-high ~10.2%
reclassified, VUS-low skewed strongly benign and none reaching P/LP); and that
reanalysis raises the downstream ethical problem of *recontact* (Thummala et al.
2024 scoping review [37]). Surveys show reanalysis is offered by most US labs but is
highly variable, usually provider-initiated, and opaque (Frees et al. 2025 [39]).
The closest published *systems* analogue to ReClass's monitoring layer is Genome
Alert! (Yauy et al., *Genetics in Medicine*, 2022 [38]), a standardized procedure
for automated genomic variant reinterpretation and gene-phenotype reassessment that
detects ClinVar-release-to-release classification changes (median ~1,247 clinically
significant changes/month).

**Implication for ReClass.** This literature establishes that (a) reanalysis is
worth doing, and (b) the operational hard part is doing it *efficiently and
auditably* — distinguishing "evidence changed but the tier did not" from a genuine
tier crossing that warrants clinician action. ReClass operationalizes precisely
that distinction: reanalysis is triggered by provider-version / evidence /
config-version change (the delta-tracking strategy Li et al. [35] validate),
same-tier changes are recorded as audit events without paging, and clinical alerts
fire **only** on tier crossings (which Kobayashi et al. [34] and Bennett et al. [36]
show are the rare, high-signal events), with old/new evidence-bundle receipts
linked. That turns the reanalysis literature's findings into a reproducible,
receipt-backed mechanism.

### 2.5 Discordance, reproducibility, provenance, and interoperability

ClinVar [15] aggregates submitted interpretations and evidence but does not
independently curate or modify submitted classifications; ClinGen's Evidence
Repository [15] provides expert-panel curated assertions through the ClinGen Variant
Curation Interface [44]. Harrison et al. 2017 [28] showed that clinical
laboratories can resolve many interpretation differences by sharing evidence and
reassessing variants (inter-lab concordance rose 88.3% → 91.7%), and Amendola et
al. 2020 [29] (the CSER consortium) found substantial inter-site variation under
ACMG/AMP application across nine genomic implementation studies, improving after
review. The recurring pressure points are the same: incomplete evidence, evolving
guidance, gene/disease-specific criteria, source-version drift, and differences in
how evidence is weighted or applied.

Three adjacent literatures motivate ReClass's systems choices:

- **Provenance / FAIR / auditability.** Reproducible genome interpretation
  requires explicit provenance and version capture. The canonical anchor is the FAIR
  Guiding Principles (Wilkinson et al. 2016 [40]); ongoing work extends FAIRness and
  sustainability across the genomic-resource ecosystem [40]. ReClass's per-point
  attribution, provider-version capture, and SHA-256 reconstruction receipt are a
  concrete instance of this direction.
- **Standardized digest-based identity.** The GA4GH Variation Representation
  Specification (VRS; Wagner et al. 2021 [41]) defines globally consistent,
  *computed* variant identifiers via normalization plus a truncated-SHA-512 digest —
  a direct standards parallel to ReClass's hashing discipline. **VRS 2.0 is now a
  formally released standard** (2.0.0/2.0.1, March 2024; 2.1 in development, snapshots
  Feb 2026) and added structural-variation support, compact ambiguous-variant
  representation, metadata integration, and extensibility [41] — broadening the class
  of variants that can carry a computed identity. The key difference from ReClass
  remains the object being hashed: VRS digests a *variant*, whereas ReClass's
  `reconstruction_hash` digests a *classification* (canonical evidence + engine/config
  version). ReClass effectively extends digest-based identity from the variant to the
  whole interpretation receipt, and a mature VRS 2.0 makes the federated-identity
  pairing in open question 8 (carry a VRS allele id alongside the receipt) more
  concretely actionable.
- **Clinical-data interoperability.** The HL7 FHIR Genomics Reporting
  Implementation Guide [42] (current *published* version v3.0.0 / STU3, published
  2024-12-12; **v4.0.0 now in active continuous build, snapshot 2026-01-30**, not yet
  balloted/published) standardizes how variants, annotations, and interpretations are
  exchanged with EHR/LIS systems (with tooling such as vcf2fhir [43] bridging VCF to
  FHIR). ReClass's deterministic FHIR Genomics serializer (`reporting/fhir.py`)
  targets the published v3.0.0 standard and derives every resource id from the variant
  key + `reconstruction_hash`, so a reconstructable classification is emitted in an
  interoperable, byte-stable form rather than a bespoke report blob.

**Implication for ReClass.** ReClass's core design responds to this literature by
making every point attributable, every source version visible, every stored
classification replayable under the recorded engine/config version, and the result
exportable in a standard interoperability format whose identifiers trace back to the
deterministic receipt.

---

## 3. Where ReClass sits relative to the literature

| Dimension | Typical adjacent tool or paper | ReClass contribution |
|---|---|---|
| Classification vocabulary | ACMG/AMP criteria and five tiers | Same vocabulary; not novel. |
| Scoring framework | ACMG combining rules or Tavtigian/Bayesian points | Tavtigian 2020 points; not novel. |
| Computational evidence | Tool-specific predictors or Pejaver/ClinGen PP3/BP4 calibration | Uses Pejaver-calibrated REVEL bins, AlphaMissense/conservation extensions, and a documented one-event REVEL+AlphaMissense consensus rule (no predictor stacking). |
| Output artifact | Tier plus criteria/rationale | Tier, points, per-criterion contributions, overrides, provider versions, source records, warnings, engine/config version, SHA-256 reconstruction hash. |
| Reproducibility | Usually code rerun or logs; LLM tools are self-consistent at best but not byte-replayable | Explicit cryptographic reconstruction receipt over canonical evidence + engine version, plus a verifier that replays persisted evidence and detects tampering. |
| Variant identity | Coordinate strings; increasingly GA4GH VRS digests | Canonical provider/storage keys; receipt-level digest extends VRS-style digest identity from variant to classification. |
| Benchmark framing | Headline agreement vs ClinVar/eRepo | Same engine on complete ClinGen criteria, sparse ClinVar signals, and ClinVar enriched with ClinGen matches — to separate scoring behavior from evidence availability. |
| Evidence provenance | Often human-readable explanation | `EvidenceBundle` with provider versions, source records, warnings, match route, and stable JSON round-trip. |
| Reanalysis / monitoring | Reanalysis shown to yield value [33-39] | Versioned-trigger reanalysis with same-tier audit events vs tier-crossing alerts and linked old/new receipts. |
| Interoperability | Bespoke reports | Deterministic HL7 FHIR Genomics export [42] with receipt-derived ids. |
| Data governance | Usually out of scope | Structural clinical/research schema separation + PostgreSQL RLS tenant isolation (two schemas, six policies). |
| Workflow | Command-line or hosted classification | API, draft persistence, credentialed sign-off, reports, reanalysis queue, alerts, reviewer frontend. |

---

## 4. Research extensions and novel findings accomplished here

### 4.1 Versioned cryptographic reconstruction receipts

**Claim.** ReClass implements a classification receipt that can be re-derived
byte-for-byte from recorded evidence and engine/config version.

**Project evidence.** `engine/scoring.py` declares `classify()` a pure function of
evidence and configuration (no I/O, network, randomness, or wall-clock), sorts and
canonicalizes the evidence to stable JSON, and computes
`reconstruction_hash = SHA-256(engine_version + "|" + canonical_evidence)` — where
`engine_version` carries the config fingerprint, so the receipt binds the tier to a
specific evidence set *and* a specific engine/config identity. `storage/verify.py`
re-runs `classify()` on persisted evidence under the recorded `engine_version` and
asserts that both the stored tier and the stored hash match (`verify_events`,
`verify_classification`); `tests/test_storage.py` verifies successful
reconstruction and detects tampered receipts and bundle provenance.

**Extension beyond literature.** Adjacent tools emphasize rule rationale and
classification transparency; the Costa et al. review [27] names limited
auditability as a recurring pitfall, and the emerging LLM classifiers [25] cannot
guarantee determinism. GA4GH VRS [41] establishes digest-based identity but for
*variants*. ReClass goes a step further by making a historical *classification* into
a cryptographic receipt: "this exact evidence under this exact engine/config version
produced this exact tier." That is a systems-level answer to the
reproducibility/discordance problem documented across the ClinVar, ClinGen, and
laboratory-concordance literature.

### 4.2 Controlled attribution of failure to evidence completeness

**Claim.** The validation design isolates scoring logic from evidence availability.

**Project evidence.** The same deterministic engine/config produces 94.7%
definitive concordance on `clingen_real_v1` (expert-applied ClinGen criteria) but
only 5.0% on `clinvar_real_v1` (sparse public signals mostly limited to
REVEL/frequency). No threshold change explains the difference; only the evidence
condition changes. The `clingen_real_v1` failure analysis confirms the mechanism:
the dominant gaps are "no pathogenic criteria supplied" or "criteria present but
below the tier threshold; needs a strength upgrade (PVS1/PS3-class)", not arithmetic
error.

**Extension beyond literature.** Many tool papers report a single benchmark metric
against ClinVar or eRepo. ReClass's paired design makes a narrower but more
informative finding: the point model can reproduce expert-panel tiers when the
expert evidence is present, and it collapses toward VUS when evidence is sparse. The
bottleneck is evidence recovery and curation, not arithmetic.

### 4.3 Measured lift from cross-source evidence transfer

**Claim.** Direct ClinGen-to-ClinVar evidence enrichment materially improves
concordance and reduces serious errors.

**Project evidence.** In `comparison_clinvar_real_v1_vs_clinvar_enriched_v1.md`,
adding matched ClinGen-applied criteria to 11,970 of 21,638 ClinVar records
(10,649 via direct ClinVar Variation ID + 940 via canonical SNV-key fallback +
381 via genomic-HGVS fallback; 37,873 criteria added in total) moved definitive
concordance from 5.0% to 42.4%
(+37.4 pp), overall exact concordance from 19.9% to 46.6% (+26.7 pp), and serious
discordance count from 34 to 6. Pathogenic recall moved from 0% to 32.1%; Likely
Pathogenic recall from 0% to 55.9%. The transfer is not free: 1,023 cases worsened
(mostly former VUS-by-default now overshooting), which the report quantifies
case-by-case.

**Which reclassifications the enrichment produced.** Because the fixture
population, engine, and config are held fixed, every changed call is attributable
to the transferred ClinGen criteria alone. Relative to the unenriched baseline,
6,807 cases *became* an exact match to the reference tier and 1,019 *lost* a
previously exact one (net +5,788 exact, consistent with the 19.9% -> 46.6%
headline; 6,993 cases moved closer to the reference and 1,023 moved farther). Read
directly from the comparison report's confusion-matrix deltas (after - before), the
corrective reclassifications are overwhelmingly movements *out of the
VUS-by-default trap* into the reference tier:

| Reference tier | Newly exact-correct (cases) | No longer mis-called VUS (cases) |
|---|---:|---:|
| Pathogenic | +3,042 | -3,180 |
| Likely Pathogenic | +1,557 | -2,505 |
| Likely Benign | +1,052 | -1,149 |
| Benign | +1,011 | -693 |

The regressions are narrow and one-directional: 874 reference-VUS cases left the
(correct) VUS cell, of which 719 were over-called Likely Pathogenic and 15
Pathogenic once transferred pathogenic criteria pushed an otherwise-VUS score past
the +6 cutoff (a further 964 *Likely Pathogenic*-reference cases were over-called
Pathogenic by one tier). That is the precise shape of the "not free" cost: the
enrichment converts a large amount of VUS-default under-calling into correct
definitive calls, at the price of a smaller amount of pathogenic-side over-calling.
None of these are clinical re-issues; each is a change in the deterministic
engine's tier between the unenriched and enriched evidence conditions, fully
reconstructable from the recorded evidence.

**Extension beyond literature.** Harrison et al. [28] showed that evidence sharing
can resolve discordance. ReClass operationalizes that idea in a reproducible
pipeline — same fixture population, same engine, direct-ID ClinGen enrichment plus
identity fallbacks, measured before/after deltas. The remaining failure is also
informative:
canonical SNV-key fallback now contributes 940 matches and genomic-HGVS fallback
contributes 381 matches on top of the 10,649 direct matches, while native
reference-backed indel-key fallback is still 0 on current real data.
The next research lever is source identity and evidence coverage, not scoring.

### 4.4 Empirical rediscovery of founder/frequency exception failure modes

**Claim.** A naive global BA1 stand-alone benign rule creates serious errors for
known high-carrier pathogenic/founder contexts.

**Project evidence.** `failure_analysis_clingen_real_v1.md` shows four serious
errors in the ClinGen benchmark, three of them Pathogenic → Benign cases in which a
BA1 stand-alone benign override (`engine/scoring.py`, the `has_ba1` short-circuit)
overrode pathogenic ClinGen evidence — the Hearing Loss VCEP founder variants GJB2
c.35delG, GJB2 c.167delT, and SLC26A4 c.349C>T. The code supports reviewable
overrides in `engine/configs/base_v1.json`; the report's clinical-release state is
`governance_reviewed_pending_credentialed_signoff`, and the Hearing Loss GJB2/SLC26A4
BA1/BS1 thresholds are flagged against the current ClinGen Hearing Loss CSpec for
correction pending credentialed clinical sign-off.

**The specific reclassifications (named).** Enumerated from the two
failure-analysis reports, the serious erroneous reclassifications the engine
produced (reference tier -> engine tier; all references are ClinGen/expert-panel
curated) are:

| Variant (ClinVar ID) | Gene / VCEP | Reference -> Engine | Why |
|---|---|---|---|
| NM_004004.5(GJB2):c.35delG p.Gly12Valfs (17004) | GJB2 / Hearing Loss | Pathogenic -> Benign | BA1 stand-alone override beats PVS1+PS4+PM3 (point sum +6) |
| NM_004004.5(GJB2):c.167delT p.Leu56Argfs (17010) | GJB2 / Hearing Loss | Pathogenic -> Benign | BA1 stand-alone override beats PVS1+PM3 (point sum +2) |
| NM_000441.1(SLC26A4):c.349C>T (43555) | SLC26A4 / Hearing Loss | Pathogenic -> Benign | BA1 stand-alone override beats PP1+PM3+PP3+PP4 |
| NM_175914.5(HNF4A):c.340C>T p.Arg114Trp (9212) | HNF4A / Monogenic Diabetes | Likely Pathogenic -> Likely Benign | net -3 pts: BS1+BS2 (strong) + BP5 outweigh PP1+PP3+PP4 |

The first three (the BA1 *conflict* cases) recur as serious errors in the enriched
ClinVar benchmark (`failure_analysis_clinvar_enriched_v1.md`); the HNF4A
*strength-mismatch* case does not recur there. Three *additional* serious
reclassifications appear only in the enriched run -- BRCA1 expert-panel Pathogenic
variants CV-55432, CV-54758, and CV-266331 -- which the enrichment failed to match
to any transferred ClinGen criteria, leaving the engine to fall back on REVEL BP4
alone (REVEL 0.061-0.169) and reclassify each Pathogenic -> Likely Benign. These
are *evidence-absence* reclassifications, categorically different from the BA1
conflict cases, and they account for why the enriched serious-error count is 6
(3 founder-BA1 + 3 BRCA1-evidence-absence) rather than 3. All seven are
flagged for credentialed clinical review, not auto-corrected in code.

**Extension beyond literature.** ClinGen already recognizes BA1 exceptions and
VCEP-specific frequency thresholds. ReClass does not discover the exception as new
biology. What it accomplishes is to reproduce the failure mode from data using a
generic config, quantify its seriousness (a stand-alone rule that silently overrides
strong pathogenic evidence), and show why overrides must be versioned, reviewable,
and auditable rather than hidden in code.

### 4.5 Provenance-rich evidence bundles as a first-class artifact

**Claim.** ReClass preserves the full evidence bundle that produced a
classification, not merely the final criteria list.

**Project evidence.** `evidence/model.py` defines `EvidenceBundle` with events,
provider versions, source records, warnings, and match metadata, with a stable JSON
round-trip and a bundle reconstruction hash. `storage/verify.py`'s
`verify_bundle_provenance` re-derives the hash from stored events, checks it against
both the bundle's persisted hash and the receipt's hash, and confirms the
provenance metadata (provider versions, warnings, match) survived persistence
byte-for-byte — i.e. it detects silent post-hoc edits to the evidence.

**Extension beyond literature.** Many tools emit criteria/rationales. ReClass
treats provider versions and source-record linkage as part of the scientific object
being evaluated, which is exactly what the provenance/FAIR literature [40] argues
for. This is what enables principled reanalysis when a provider, config, or source
changes.

### 4.6 Auditable reanalysis that separates evidence drift from tier crossings

**Claim.** ReClass turns the reanalysis literature's findings into a reproducible,
receipt-backed mechanism that pages humans only when a clinically meaningful tier
boundary is crossed.

**Project evidence.** `monitoring/diff.py` computes tier-crossing diffs;
`monitoring/reanalysis.py` and `ops/` implement a reanalysis queue, scheduler, and
run reports keyed on provider-version / evidence / config-version triggers;
`storage/alerts.py` records same-tier changes as audit events and creates alerts
**only** on tier crossings, linking old/new evidence-bundle receipts when
available. The reanalysis tables are tenant-scoped and RLS-protected like the rest
of `clinical.*`.

**Extension beyond literature.** Reanalysis studies [33-37,39] quantify *that*
reanalysis yields clinically relevant changes; Genome Alert! [38] standardizes the
*procedure*; Li et al. [35] show delta-tracking is more efficient than blind
recompute. ReClass's addition is to bind each reanalysis step to reconstructable
receipts and to make the same-tier-vs-tier-crossing distinction an explicit, audited
state transition rather than a manual judgement.

### 4.7 Clinical/research governance integrated with classification

**Claim.** ReClass pairs classification receipts with privacy-preserving storage
boundaries.

**Project evidence.** `db/schema.sql` defines two schemas — `clinical` (identified,
tenant-scoped patients, classifications, sign-off, alerts, reanalysis) and
`research` (de-identified variant evidence, bundles, source records, cohort counts)
— with six row-level-security policies (`tenant_isolation_patient`,
`_classification`, `_alert`, `_reanalysis`, `_queue`, `_run`). Tests assert tenant
isolation, absence of patient/tenant identifiers in research tables, and no
foreign-key path from research back to clinical.

**Extension beyond literature.** Most academic classifiers stop at outputting a
call. ReClass makes tenant isolation, de-identified evidence persistence,
verification, reanalysis, and sign-off part of the same model. That is a systems
contribution, not a new ACMG method.

### 4.8 Offline deterministic validation artifacts

**Claim.** Once fixtures are built, scoring and validation rerun without network
calls, clocks, randomness, or live source drift.

**Project evidence.** The scoring core is pure; committed fixtures live under
`validation/fixtures/`; generated validation reports are reproducible artifacts
under `validation/reports/`; diagnostic plots are written under `plots/`; source
governance is documented. The 877-test suite is green in this environment, and
storage/reanalysis tests that need PostgreSQL skip cleanly when unavailable.

**Extension beyond literature.** This addresses a known reproducibility weakness in
annotation-driven tools (live databases and changing snapshots can silently change
results), making source refreshes explicit and versioned.

---

## 5. What is not novel

- The ACMG/AMP five-tier framework is not new.
- The Bayesian/point scoring model is Tavtigian/ClinGen SVI, not invented here.
- REVEL/AlphaMissense PP3/BP4 thresholds come from ClinGen/Pejaver/Bergquist
  calibration, not a new local calibration.
- Automated ACMG classification already exists (InterVar, CharGer, TAPES, GeneBe,
  BIAS-2015, VarSome, AutoPVS1, and the new LLM-based assistants).
- Digest-based identity already exists as a standard (GA4GH VRS); ReClass's
  contribution is applying that discipline to the classification receipt, not the
  hashing idea itself.
- Automated reanalysis exists (e.g. Genome Alert!); ReClass's contribution is the
  receipt-bound, same-tier-vs-crossing formulation, not reanalysis itself.
- Concordance with ClinGen or ClinVar is not biological ground truth.
- The project does not read the literature, evaluate functional assays, infer
  segregation, judge phenotype specificity, or clinically validate a patient report.
- Source coverage is narrow, especially outside missense SNVs, small variants with
  coordinates, and cases where ClinGen/REVEL/AlphaMissense/gnomAD/cohort or
  reviewer-supplied structured signals are available.

---

## 6. Main research conclusion

The central finding is:

> Deterministic ACMG/AMP point scoring is not the primary blocker. Evidence
> completeness, source identity, provenance, gene/disease-specific rules, and
> auditable reanalysis are the primary blockers.

ReClass supports this by running the same engine under three evidence conditions:

1. Expert-applied ClinGen criteria -> high concordance (94.7% definitive).
2. Sparse ClinVar public signals -> poor concordance (5.0% definitive).
3. ClinVar plus matched ClinGen evidence -> large but incomplete improvement
   (42.4% definitive).

This is a genuine research extension because it changes the question from "can a
rule engine classify variants?" to "which missing evidence and provenance links
prevent a deterministic engine from reproducing expert assertions, and how do we
keep the answer reconstructable and auditable as evidence and guidance change?" In a
field adding LLM classifiers whose outputs cannot be byte-for-byte replayed, the
demonstration that a *reconstructable* engine's gap is evidence rather than
arithmetic is itself a useful negative-control result.

The mid-2026 emergence of the **ACMG/AMP/CAP/ClinGen SVC v4.0** standard reinforces
this framing rather than undercutting it: the field's official next guideline is
itself a Bayesian, points-based system that subdivides VUS by likelihood, so the
scoring substrate ReClass chose is converging *toward*, not away from, the standard.
That makes the project's contribution — reconstructable, provenance-bound, governed
evidence handling around a points core — the part that remains scarce, and reframes
the headline gap (collapse to VUS on sparse evidence) as the natural place to adopt
SVC v4.0's graded VUS sub-tier once it publishes (open question 10).

---

## 7. Open research questions set up by this project

1. **Evidence recovery and identity matching.** How much additional lift appears
   when ClinGen/eRepo source records expose usable loci, canonical-key fallback is
   populated, and reference-backed indel normalization runs against a production
   GRCh38 FASTA? (Partly answered: canonical SNV-key fallback is now populated
   (+940 matches), genomic-HGVS fallback contributes +381 matches, and a local
   GRCh38 FASTA is installed, but native reference-backed indel-key lift is still
   0 on current real data.)

2. **Conflict resolution.** What deterministic, auditable policy should govern
   conflicts such as BA1/BS1 population evidence versus curated pathogenic
   founder-variant evidence? (The clingen_real_v1 serious errors show the cost of
   getting this wrong.)

3. **VCEP-specific configuration.** Which current VCEP CSpecs [30-32], BA1/BS1
   thresholds, PS4/PM3 count rules, and local lab policies close the remaining
   serious-error gap without sacrificing reconstructability?

4. **Broader evidence population.** Structured providers now exist for splice,
   indel, functional, segregation, phenotype, CNV, mitochondrial, non-coding,
   structural-variant, and repeat-expansion evidence. How should the system populate
   them from validated sources (e.g. the ClinGen SVI splicing recommendations [12])
   while preserving versioned receipts and avoiding autonomous overclaiming?

5. **Phenotype-aware extensions.** The 2026 comparative-tool literature [26]
   suggests phenotype-aware prioritization can outperform purely genomic tools. What
   is the right boundary between ReClass as a variant-evidence calculator and
   phenotype-aware case interpretation?

6. **Longitudinal reanalysis governance.** Given the reanalysis literature [33-39],
   how should a clinical service manage evidence/config/source-version changes at
   cohort scale, distinguishing same-tier evidence changes from tier-crossing alerts
   while keeping the recontact decision auditable?

7. **Equity and frequency evidence.** How should ancestry-specific frequency, local
   ancestry, under-sampled populations, and founder effects be represented so that
   BA1/BS1/PM2 reasoning does not overstate certainty? (Sharpened by Kore et al. 2025
   [14b]: local-ancestry inference reveals ≥2-fold ancestry-specific frequency
   differences for the majority of variants in admixed gnomAD groups, so an aggregated
   popmax FAF can both over- and under-state a frequency criterion. Could ReClass
   carry ancestry-resolved FAF as a provenance-bearing signal, and would that have
   prevented any of the founder-variant BA1 conflicts in §4.4?)

8. **Interoperability fidelity.** Does the deterministic FHIR Genomics export [42]
   preserve enough of the reconstruction receipt (criteria, points, versions, hash)
   to let a downstream EHR/LIS re-verify a classification, or only display it? Could
   GA4GH VRS [41] identifiers be carried alongside the receipt for federated
   identity?

9. **Determinism vs LLM assistance.** Where LLM-based criterion extraction (e.g.
   AutoPM3 [24]) or whole-classification LLM frameworks [25] add coverage, how can
   their (unreplayable) outputs enter a reconstructable receipt without breaking the
   byte-for-byte guarantee — e.g. as versioned, human-signed evidence events rather
   than as the classifier itself? The 2025 "precision grounding" result [25] suggests
   the tractable form is LLM-as-grounded-evidence-extractor, with the extracted event
   (and its source) carried in the bundle and the points still assigned by the
   deterministic core.

10. **Alignment with the forthcoming SVC v4.0 standard.** When the
    ACMG/AMP/CAP/ClinGen SVC v4.0 standard [7] publishes, what is the minimal,
    reconstructability-preserving way to adopt its two structural changes — the
    re-labeled, concept-driven evidence codes (a new reviewable config version mapping
    codes to the existing point core) and the **graded VUS sub-tier** (replacing
    ReClass's single VUS bin, which is its dominant sparse-evidence failure mode in
    §4.2, with a likelihood-banded VUS)? Does mapping point totals to SVC v4.0's VUS
    sub-bands change any of the serious-discordance cases in §4.4, and can the engine
    emit both the legacy five-tier and the v4.0 graded tier from one receipt during a
    transition period?

---

## 8. Key references and sources consulted

*Citations were re-verified on 2026-06-17 (authors, venue, year, and a working
PMID/DOI/URL), with a 2026-06-19 freshness pass that added refs 14b and updated refs
7, 14, 25, 41, and 42 against primary sources (ACMG Documents-in-Development, ACMG
2026 abstract P593, gnomAD/Nature Communications, GA4GH VRS releases, HL7 FHIR
build). Corrections and status updates are noted inline.*

### Foundational ACMG/AMP and quantitative frameworks

1. Richards S, Aziz N, Bale S, et al. Standards and guidelines for the
   interpretation of sequence variants. *Genetics in Medicine* 2015;17(5):405-424.
   PMID 25741868. DOI 10.1038/gim.2015.30.

2. Tavtigian SV, Greenblatt MS, Harrison SM, et al. Modeling the ACMG/AMP variant
   classification guidelines as a Bayesian classification framework. *Genetics in
   Medicine* 2018;20(9):1054-1060. PMID 29300386. DOI 10.1038/gim.2017.210.

3. Tavtigian SV, Harrison SM, Boucher KM, Biesecker LG. Fitting a naturally scaled
   point system to the ACMG/AMP variant classification guidelines. *Human Mutation*
   2020;41(10):1734-1737. PMID 32720330. DOI 10.1002/humu.24088.

4. Horak P, Griffith M, Danos AM, et al. Standards for the classification of
   pathogenicity of somatic variants in cancer (oncogenicity): joint recommendations
   of ClinGen, CGC, and VICC. *Genetics in Medicine* 2022;24(5):986-998. PMID
   35101336. DOI 10.1016/j.gim.2022.01.001. *(Reuses the Tavtigian point shorthand
   for a parallel somatic-oncogenicity scale.)*

5. ClinGen Variant Classification Guidance (current canonical combining-rules hub;
   last updated July 2025).
   https://clinicalgenome.org/tools/clingen-variant-classification-guidance/

6. ClinGen Sequence Variant Interpretation (SVI) Working Group page — **retired
   April 2025**, redirecting to ref 5.
   https://clinicalgenome.org/working-groups/sequence-variant-interpretation/

7. ACMG/AMP/CAP/ClinGen Sequence Variant Classification standard, **"SVC v4.0"**
   (Biesecker LG & Harrison SM, co-chairs) — a Bayesian, points-based system with flow
   diagrams, a graded VUS sub-tier, and an overhauled concept-driven evidence-code set.
   **In development and unpublished as of mid-2026** (ACMG "Documents in Development",
   "will soon be released"). Design and pilot results are public via the ACMG 2026
   meeting abstract: Biesecker LG, Rehm HL, Abou Tayoun A, Berg JS, Bick D, Byrne AB,
   Chao EC, Gastier-Foster JM, Karbassi I, Moyer AM, O'Donnell-Luria A, Plon SE, Shah
   N, Vincent LM, Whiffin N, Harrison SM. "Piloting the forthcoming ACMG/AMP/CAP/ClinGen
   standards for sequence variant classification." *Genetics in Medicine* 2026
   (abstract **P593**; presented 2026-03-12; pilot: 17/20 variants reached >90%
   concordance on the three-level scale). Cite only as an in-development standard and a
   conference abstract, not a published guideline.

### Evidence calibration and source resources

8. Pejaver V, Byrne AB, Feng BJ, et al. Calibration of computational tools for
   missense variant pathogenicity classification and ClinGen recommendations for
   PP3/BP4 criteria. *American Journal of Human Genetics* 2022;109(12):2163-2177.
   PMID 36413997. DOI 10.1016/j.ajhg.2022.10.013. PMC9748256.

9. Bergquist T, Stenton SL, Nadeau EAW, et al. Calibration of additional
   computational tools expands ClinGen recommendation options for variant
   classification with PP3/BP4 criteria (AlphaMissense, ESM1b, VARITY). *Genetics in
   Medicine* 2025;27(6):101402. PMID 39345488. DOI 10.1016/j.gim.2025.101402.
   *(Developer-default AlphaMissense 0.564 / ESM1b −7.5 do not reach Supporting under
   calibration.)*

10. Brnich SE, Abou Tayoun AN, Couch FJ, et al. Recommendations for application of
    the functional evidence PS3/BS3 criterion using the ACMG/AMP sequence variant
    interpretation framework. *Genome Medicine* 2019;11(1):3. PMID 31892348. DOI
    10.1186/s13073-019-0690-2.

11. Biesecker LG, Byrne AB, Harrison SM, et al. ClinGen guidance for use of the
    PP1/BS4 co-segregation and PP4 phenotype specificity criteria for sequence
    variant pathogenicity classification. *American Journal of Human Genetics*
    2024;111(1):24-38. PMID 38103548. DOI 10.1016/j.ajhg.2023.11.009. *(Corrected
    from the prior draft's "Jarvik/Browning" attribution.)*

12. Walker LC, de la Hoya M, Wiggins GAR, et al. Using the ACMG/AMP framework to
    capture evidence related to predicted and observed impact on splicing:
    recommendations from the ClinGen SVI Splicing Subgroup. *American Journal of
    Human Genetics* 2023;110(7):1046-1067. PMID 37352859. DOI
    10.1016/j.ajhg.2023.06.002. *(SpliceAI delta thresholds; basis for the engine's
    splice mapper.)*

13. Ioannidis NM, Rothstein JH, Pejaver V, et al. REVEL: an ensemble method for
    predicting the pathogenicity of rare missense variants. *American Journal of
    Human Genetics* 2016;99(4):877-885. PMID 27666373. DOI
    10.1016/j.ajhg.2016.08.016.

14. gnomAD v4.1 release notes (joint allele numbers; discordant exome/genome flags),
    April 2024. https://gnomad.broadinstitute.org/news/2024-04-gnomad-v4-1/ ;
    Karczewski KJ, et al. The mutational constraint spectrum quantified from
    variation in 141,456 humans. *Nature* 2020;581(7809):434-443. PMID 32461654. DOI
    10.1038/s41586-020-2308-7. *(gnomAD v5 not released as of mid-2026; v4.1 current.)*

14b. Kore P, Wilson MW, Tiao G, et al. Improved allele frequencies in gnomAD through
    local ancestry inference. *Nature Communications* 2025;16(1):8734. PMID 41053080.
    DOI 10.1038/s41467-025-63340-2. *(≥2-fold ancestry-specific frequency differences
    for 78.5% / 85.1% of variants in admixed Admixed-American / African-American
    groups; basis for open question 7.)*

15. NCBI ClinVar introduction (https://www.ncbi.nlm.nih.gov/clinvar/intro/) and
    ClinGen Evidence Repository / ERepo (https://erepo.clinicalgenome.org/evrepo/).

### Automated and semi-automated tools

16. Li Q, Wang K. InterVar: clinical interpretation of genetic variants by the 2015
    ACMG-AMP guidelines. *American Journal of Human Genetics* 2017;100(2):267-280.
    PMID 28132688. DOI 10.1016/j.ajhg.2017.01.004. Web tool:
    https://wintervar.wglab.org/

17. Scott AD, Huang KL, Weerasinghe A, et al. CharGer: clinical Characterization of
    Germline variants. *Bioinformatics* 2019;35(5):865-867. DOI
    10.1093/bioinformatics/bty649.

18. Xavier A, Scott RJ, Talseth-Palmer BA. TAPES: a tool for assessment and
    prioritisation in exome studies. *PLOS Computational Biology*
    2019;15(10):e1007453. DOI 10.1371/journal.pcbi.1007453.

19. Stawiński P, Płoski R. Genebe.net: implementation and validation of an automatic
    ACMG variant pathogenicity criteria assignment. *Clinical Genetics*
    2024;106(2):119-126. PMID 38440907. DOI 10.1111/cge.14516. Docs:
    https://docs.genebe.net/docs/acmg/

20. Eisenhart C, Brickey R, Nadon B, Mewton J, Bayat V. Automating ACMG variant
    classifications with BIAS-2015 v2.1.1: algorithm analysis and benchmark against
    the FDA-approved eRepo dataset. *Genome Medicine* 2025. DOI
    10.1186/s13073-025-01581-y. PMC12706976.

21. Xiang J, Peng J, Baxter S, Peng Z. AutoPVS1: an automatic classification tool
    for PVS1 interpretation of null variants. *Human Mutation* 2020;41(9):1488-1498.
    PMID 32442321. DOI 10.1002/humu.24051. *(Author list corrected from the prior
    draft.)*

22. VarSome germline classifier implementation notes.
    https://varsome.com/about/resources/germline-implementation/

23. OpenCRAVAT calibrated classification package for ACMG/AMP computational
    predictors (2025). https://www.opencravat.org/calibrated-classification-package-applying-acmg-amp-guidelines-to-computational-predictors-in-opencravat/

24. Li S, Wang Y, Liu CM, et al. AutoPM3: enhancing variant interpretation via
    LLM-driven PM3 evidence extraction from scientific literature. *Bioinformatics*
    2025;41(7):btaf382. DOI 10.1093/bioinformatics/btaf382. PMC12263107. *(A
    criterion-specific LLM assist; relevant to open question 9.)*

25. LLM-based ACMG classification (the unreplayable paradigm). (a) Ma W, et al.
    "DeepSeek as the paradigm shift in rare disease diagnosis — a fully automated
    genetic variant classification system." *medRxiv* 2025.06.03.25328923
    (**preprint**); documented GPT-4 performance drift in variant assessment
    (arXiv:2312.13521). (b) Benchmarking GPT-4o, Llama-3.1, and Qwen-2.5 for cancer
    genetic variant classification. *npj Precision Oncology* 2025;9:s41698-025-00935-4
    *(high self-consistency >90% on repeat, GPT-4o ~0.73 accuracy, three-model
    consensus ~0.97 only on the 26% where all agree; O1/Claude-3.5-Sonnet/DeepSeek-R1
    occasionally misclassify pathogenicity).* (c) "Precision Grounding: Augmenting Large
    Language Models with Evidence-Based Databases for Trustworthy Genetic Variant
    Summarization." *medRxiv* 2025.06.09.25329279; PMC12204447 *(grounding in curated
    evidence DBs eliminates much hallucination — supports the LLM-as-grounded-evidence-
    extractor boundary in open question 9).* Cited as contrast/boundary cases for a
    reconstructable engine; not peer-reviewed clinical tools. The distinction is
    reconstructability (version-bound, byte-replayable receipt), not run-to-run
    determinism alone.

26. Ghasemnejad T, Liang Y, Jahanian KH, et al. Comprehensive evaluation of
    ACMG/AMP-based variant classification tools. *Bioinformatics* 2026;42(2):btaf623.
    DOI 10.1093/bioinformatics/btaf623. PMC12916173.

27. Costa M, García S A, León A, Pastor O. The promises and pitfalls of automated
    variant interpretation: a comprehensive review. *Briefings in Bioinformatics*
    2025;26(5):bbaf545. DOI 10.1093/bib/bbaf545. PMID 41071614.

### Discordance, evidence sharing, and concordance

28. Harrison SM, Dolinsky JS, Knight Johnson AE, et al. Clinical laboratories
    collaborate to resolve differences in variant interpretations submitted to
    ClinVar. *Genetics in Medicine* 2017;19(10):1096-1104. PMID 28301460. DOI
    10.1038/gim.2017.14. *(Concordance 88.3% → 91.7% after evidence sharing.)*

29. Amendola LM, Muenzen K, Biesecker LG, et al. Variant classification concordance
    using the ACMG-AMP variant interpretation guidelines across nine genomic
    implementation research studies (CSER). *American Journal of Human Genetics*
    2020;107(5):932-941. PMID 33108757.

### Gene/disease-specific VCEP specifications (2024-2025 examples)

These illustrate the active VCEP CSpec frontier that ReClass's reviewable override
mechanism mirrors; they are not implemented as automated rules here.

30. Wilcox EH, et al. Updated ACMG/AMP specifications for variant interpretation and
    gene curations from the ClinGen RASopathy expert panels. *Genetics in Medicine
    Open* 2025;3:103430. PMID 40496714. DOI 10.1016/j.gimo.2025.103430. *(Journal and
    first-author corrected from the prior draft; refines recessive criteria and
    re-tunes PP3/BP4 — not a new point system per se.)*

31. Richardson ME, Bishop MFH, Holdren MA, et al. ClinGen PALB2 Variant Curation
    Expert Panel specifications of the ACMG/AMP variant curation guidelines for
    germline PALB2. *American Journal of Human Genetics* 2025;112(10):2266-2280. PMID
    40967221.

32. Parsons MT, et al. Evidence-based recommendations for gene-specific ACMG/AMP
    variant classification from the ClinGen ENIGMA BRCA1 and BRCA2 Variant Curation
    Expert Panel. *American Journal of Human Genetics* 2024;111(9):2044-2058. PMID
    39142283.

### Variant reanalysis and reclassification

33. Bartolomaeus T, et al. Re-evaluation and re-analysis of 152 research exomes five
    years after the initial report reveals clinically relevant changes in 18%.
    *European Journal of Human Genetics* 2023;31(10):1154-1164. PMID 37460657.
    PMC10545662. *(Peer-reviewed figure is 18% — corrected from the prior draft's
    "~20%".)*

34. Kobayashi Y, Chen E, Facio FM, et al. Clinical variant reclassification in
    hereditary disease genetic testing. *JAMA Network Open* 2024;7(11):e2444526.
    PMID 39504018. PMC11541632. *(Single-laboratory study — corrected from
    "multi-laboratory"; ~4x decrease in normalized reclassification-to-VUS rate
    2015→2023.)*

35. Li L, Tian X, Woodzell V, Gibbs RA, Yuan B, Venner E. Tracking updates in
    clinical databases increases efficiency for variant reanalysis. *Genetics in
    Medicine Open* 2024;2:101841. PMID 39669589. PMC11613846.

36. Bennett G, Karbassi I, Chen W, et al. Distinct rates of VUS reclassification are
    observed when subclassifying VUS by evidence level. *Genetics in Medicine*
    2025;27(6):101400. PMID 40035215. *(Peer-reviewed version; corrected from the
    medRxiv preprint link.)*

37. Thummala A, Sudhakaran R, Gurram A, et al. Variant reclassification and recontact
    research: a scoping review. *Genetics in Medicine Open* 2024;2:101867. PMID
    39669626. PMC11613892.

38. Yauy K, Defour J, Cabanettes C, et al. Genome Alert!: a standardized procedure
    for genomic variant reinterpretation and automated gene-phenotype reassessment
    in clinical routine. ***Genetics in Medicine*** 2022;24(6):1316-1327. PMID
    35311657. DOI 10.1016/j.gim.2022.02.008. *(Journal corrected — it is Genetics in
    Medicine, not Genome Medicine.)*

39. Frees M, Carter JN, Wheeler MT, Reuter C. The current landscape of clinical
    exome and genome reanalysis in the U.S. *Journal of Genetic Counseling*
    2025;34(2):e1968. PMID 39285507. *(Reanalysis is widely offered but variable,
    provider-initiated, and opaque — the gap an automated audited subsystem fills.)*

### Provenance, reproducibility, identity, and clinical-data interoperability

40. Wilkinson MD, Dumontier M, Aalbersberg IJ, et al. The FAIR Guiding Principles
    for scientific data management and stewardship. *Scientific Data*
    2016;3:160018. DOI 10.1038/sdata.2016.18. *(Canonical FAIR anchor.)* See also
    Babb L, et al. Improving the FAIRness and sustainability of the NHGRI resources
    ecosystem, 2025 (PMID 40895087; arXiv 2508.13498, preprint).

41. Wagner AH, Babb L, Alterovitz G, et al. The GA4GH Variation Representation
    Specification: a computational framework for variation representation and
    federated identification of molecular variation. *Cell Genomics*
    2021;1(2):100027. PMID 35311178. DOI 10.1016/j.xgen.2021.100027. *(Digest-based
    variant identity — the closest standards parallel to ReClass's reconstruction
    receipt.)* **VRS 2.0 is now formally released** (2.0.0 2024-03-14 / 2.0.1
    2024-03-20; 2.1 in development, snapshots Feb 2026), adding structural-variation
    support, compact ambiguous representation, metadata integration, and extensibility.
    https://vrs.ga4gh.org/ ; https://github.com/ga4gh/vrs/releases

42. HL7 FHIR Genomics Reporting Implementation Guide — current published version
    v3.0.0 (STU3), published 2024-12-12 (https://hl7.org/fhir/uv/genomics-reporting/);
    **v4.0.0 in active continuous build** (snapshot 2026-01-30, FHIR R4-based, not yet
    balloted/published — https://build.fhir.org/ig/HL7/genomics-reporting/). The
    serializer targets the published v3.0.0.

43. Dolin RH, Boxwala A, Shalaby J. vcf2fhir: a utility to convert VCF files into
    HL7 FHIR format for genomics-EHR integration. *BMC Bioinformatics*
    2021;22(1):104. PMID 33653260. DOI 10.1186/s12859-021-04039-1. PMC7923512.
    *(Year corrected from 2020 to 2021.)*

44. Preston CG, Wright MW, Madhavrao R, et al. ClinGen Variant Curation Interface: a
    variant classification platform for the application of evidence criteria from
    ACMG/AMP guidelines. *Genome Medicine* 2022;14(1):6. PMID 35039090. DOI
    10.1186/s13073-021-01004-8. PMC8764818. *(Correctly identified as the VCI
    platform paper.)*

---

## 9. Internal cross-references

- Project overview: `overview.md`
- Current technical status: `ReClass Model/README.md`
- Honest model boundaries: `limitations.md`
- Unfinished work: `gap.md`
- Validation reports: `ReClass Model/validation/reports/`
- Data governance: `ReClass Model/docs/data_governance.md`
