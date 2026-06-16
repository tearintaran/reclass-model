# Research Context - Literature Review and Project Contributions

*Literature adjacent to the ReClass proof of concept, and the research extension
this project has actually accomplished.*

Last reviewed: 2026-06-16.

This review positions the **Standardized Variant Reclassification Engine**
(`ReClass Model/`) against published ACMG/AMP variant-interpretation literature,
current ClinGen guidance, and adjacent automated-classification tools. It is
deliberately conservative: ReClass does **not** claim new biology, new ACMG/AMP
combining rules, or a new PP3/BP4 calibration. The contribution is best framed as
research engineering and experimental methodology around deterministic scoring,
evidence provenance, evidence-gap attribution, reconstruction, and governance.

The project evidence used here comes from:

- `ReClass Model/engine/scoring.py`
- `ReClass Model/evidence/`
- `ReClass Model/storage/verify.py`
- `ReClass Model/db/schema.sql`
- `ReClass Model/validation/reports/`
- `overview.md`, `limitations.md`, and `gap.md`

External sources are listed in the final section.

---

## 1. One-paragraph project summary

ReClass is a deterministic, auditable ACMG/AMP-style variant-classification
engine. It maps structured evidence events, selected source signals
(ClinGen-applied criteria, REVEL, gnomAD frequency, and cohort-count PS4 helpers),
and versioned configuration into a signed point total using the ClinGen
SVI/Tavtigian Bayesian point framework. It returns the five-tier result
(Pathogenic, Likely Pathogenic, VUS, Likely Benign, Benign), per-criterion
contributions, stand-alone overrides such as BA1, provider/source provenance,
engine/config version, and a SHA-256 reconstruction hash. The repository also
implements evidence bundles, ClinGen/REVEL/gnomAD providers, real-data validation
fixtures, failure analysis, ClinVar enrichment from ClinGen matches, PostgreSQL
storage with row-level tenant isolation, clinical/research data separation,
reanalysis queueing, alerting, reviewer reports, patient-safe summaries, and
credentialed human sign-off surfaces.

Validation in this snapshot:

| Benchmark | Cases | Gate | Definitive concordance | Serious discordance | Overall exact concordance |
|---|---:|---|---:|---:|---:|
| `synthetic_v1` | 25 | PASS | 90.5% | 0 | 92.0% |
| `clingen_real_v1` | 12,446 | PASS | 94.7% | 4 | 93.0% |
| `clinvar_real_v1` | 21,638 | FAIL | 5.0% | 34 | 19.9% |
| `clinvar_enriched_v1` | 21,638 | FAIL | 37.8% | 9 | 43.3% |

These are concordance-with-reference numbers, not proof of biological truth or
clinical readiness.

---

## 2. Literature landscape adjacent to ReClass

### 2.1 Foundational ACMG/AMP and quantitative frameworks

The direct foundation is the ACMG/AMP 2015 framework from Richards et al., which
defines five pathogenicity categories and 28 qualitative evidence criteria. This
is the vocabulary ReClass uses.

Tavtigian et al. 2018 showed that the qualitative ACMG/AMP combining rules are
largely compatible with a Bayesian framework. Tavtigian et al. 2020 then fitted a
naturally scaled point system to the guideline categories: supporting = 1,
moderate = 2, strong = 4, very strong = 8, with pathogenic/benign point totals
mapped to classification tiers. ReClass adopts this model rather than inventing a
new one.

Current ClinGen materials matter because they convert broad ACMG/AMP rules into
more specific, machine-readable and panel-reviewable practice. As of the current
review, ClinGen notes that the Sequence Variant Interpretation Working Group page
is archived/retired and that the active aggregated recommendation surface is the
ClinGen Variant Classification Guidance page. That page includes general guidance,
criteria-specific recommendations, BA1 exception resources, PVS1, PS3/BS3,
PP3/BP4 calibration, PM2/PM3, PP1/BS4/PP4, and endorsed point-system guidance.

**Implication for ReClass.** The project is not novel because it scores
ACMG/AMP evidence. Its novelty must be judged downstream of the score model:
deterministic reconstruction, evidence provenance, validation design, and
operational governance.

### 2.2 Criterion-specific calibration and evidence-source literature

Pejaver et al. 2022 calibrated computational predictors for ACMG/AMP PP3/BP4
evidence, including REVEL bins that can reach supporting, moderate, or strong
evidence depending on score interval. ReClass implements REVEL PP3/BP4 bins from
this literature. Later ClinGen work expanded computational calibration to
additional tools such as AlphaMissense, ESM1b, and VARITY, and OpenCRAVAT has
begun packaging calibrated predictor outputs for reproducible use. These efforts
define a natural extension path for ReClass's pluggable evidence layer.

Brnich et al. 2019/2020 provided structured recommendations for PS3/BS3
functional evidence. ClinGen also provides PVS1, PM2, PM3, PP1/BS4/PP4, splicing,
and BA1 exception guidance. These are important because many variants in real
practice are classified from functional, segregation, proband, phenotype, splice,
or mechanism-specific evidence that ReClass does not yet derive autonomously.

REVEL and gnomAD are adjacent data sources rather than classification engines.
REVEL is a missense predictor; gnomAD provides population allele-frequency
context. gnomAD v4.1 added joint allele-number reporting and warnings for
discordant exome/genome frequencies, underscoring why frequency evidence needs
source/version provenance and cannot be treated as timeless.

**Implication for ReClass.** ReClass's current automated evidence coverage is
useful but narrow: ClinGen-applied criteria, REVEL, gnomAD frequency, and
configured cohort counts. The literature points toward more calibrated providers,
not toward loosening the point thresholds.

### 2.3 Automated and semi-automated ACMG classifiers

Several tools already automate parts of ACMG/AMP interpretation:

| Tool or platform | Relevant literature/status | Relationship to ReClass |
|---|---|---|
| InterVar | Li and Wang 2017; widely used open-source ACMG/AMP implementation | Direct neighbor; auto-applies many criteria and expects human adjustment. |
| CharGer | Scott et al. 2019 | Open-source germline cancer-focused classifier with custom modules and flexible scoring. |
| TAPES | Xavier et al. 2019 | Implements ACMG assignment plus Tavtigian-style probability and cohort enrichment; benchmarked against InterVar/CharGer and ClinGen eRepo examples. |
| GeneBe | Stawinski and Ploski 2024 plus active docs | Web/API ACMG implementation with editable criteria and automated assignment for a subset of rules. |
| BIAS-2015 v2.1.1 | Eisenhart et al. 2025, Genome Medicine | Open-source, 19-criterion ACMG automation benchmarked against ClinGen eRepo; reports better pathogenic/benign sensitivity than InterVar and transparent rationales. |
| AutoPVS1 | Xiang et al. 2020 | Focuses on rigorous PVS1 strength automation rather than whole-classification provenance. |
| VarSome, Franklin, ELLA | Commercial/freemium or hosted platforms | Clinically used but not always independently reproducible or fully auditable from source. |
| OpenCRAVAT calibrated predictor package | 2025 package/blog built around ClinGen PP3/BP4 calibration | Adjacent evidence-calibration infrastructure rather than a complete ReClass-like receipt system. |

A 2026 Bioinformatics evaluation of ACMG/AMP-based tools benchmarked Franklin,
InterVar, TAPES, GeneBe, and LIRICAL on 151 Mendelian-disorder datasets. It found
that phenotype-aware approaches can outperform tools relying mainly on genomic
features for variant prioritization. This is especially relevant to ReClass
because ReClass intentionally does not automate phenotype fit or clinical
case-level evidence at this stage.

**Implication for ReClass.** The adjacent literature is crowded on automated
criteria assignment and tool benchmarking. ReClass should not claim to be the
first automated classifier. Its stronger distinction is that it treats every
classification as a reconstructable, versioned, provenance-carrying receipt and
uses matched validation fixtures to isolate evidence completeness from scoring
logic.

### 2.4 Discordance, evidence sharing, and reproducibility literature

ClinVar and ClinGen show why reproducibility matters. ClinVar aggregates submitted
variant interpretations and evidence, but NCBI states that ClinVar does not
independently curate submitted content or modify classifications outside explicit
submissions. ClinGen's Evidence Repository provides expert-panel curated
assertions and supporting evidence summaries.

Harrison et al. 2017 showed that clinical laboratories can resolve many
interpretation differences by sharing evidence and reassessing variants. Amendola
et al. 2020, across nine genomic implementation studies, found substantial
inter-site variation under ACMG/AMP application and improved concordance after
review. The literature repeatedly points to the same pressure points: incomplete
evidence, evolving guidance, gene/disease-specific criteria, source version drift,
and differences in how evidence is weighted or applied.

**Implication for ReClass.** ReClass's core design responds to this literature by
making every point attributable, every source version visible, and every stored
classification replayable under the recorded engine/config version.

---

## 3. Where ReClass sits relative to the literature

| Dimension | Typical adjacent tool or paper | ReClass contribution |
|---|---|---|
| Classification vocabulary | ACMG/AMP criteria and five tiers | Same vocabulary; not novel. |
| Scoring framework | ACMG combining rules or Tavtigian/Bayesian points | Tavtigian 2020 points; not novel. |
| Computational evidence | Tool-specific predictors or Pejaver/ClinGen PP3/BP4 calibration | Uses Pejaver-calibrated REVEL bins; extendable provider layer. |
| Output artifact | Tier plus criteria/rationale | Tier, points, per-criterion contributions, overrides, provider versions, source records, warnings, engine/config version, reconstruction hash. |
| Reproducibility | Usually code rerun or logs | Explicit SHA-256 reconstruction receipt over canonical evidence and engine version, plus verifier that replays persisted evidence. |
| Benchmark framing | Headline agreement versus ClinVar/eRepo | Same engine on complete ClinGen criteria, sparse ClinVar signals, and ClinVar enriched with ClinGen matches to separate scoring behavior from evidence availability. |
| Evidence provenance | Often present as human-readable explanation | EvidenceBundle model with provider versions, source records, warnings, match route, and stable serialization. |
| Data governance | Usually out of scope | Structural clinical/research schema separation plus PostgreSQL RLS tenant isolation tests. |
| Workflow | Often command-line or hosted classification | API, draft persistence, sign-off, reports, reanalysis queue, same-tier audit events, and tier-crossing alerts. |

---

## 4. Research extensions and novel findings accomplished here

### 4.1 Versioned cryptographic reconstruction receipts

**Claim.** ReClass implements a classification receipt that can be re-derived
byte-for-byte from recorded evidence and engine/config version.

**Project evidence.** `engine/scoring.py` declares `classify()` a pure function of
evidence and configuration, canonicalizes evidence, and creates a SHA-256
`reconstruction_hash`. `storage/verify.py` replays persisted evidence and checks
the stored tier and hash. `tests/test_storage.py` verifies successful
reconstruction and detects tampered hashes.

**Extension beyond literature.** Adjacent tools emphasize rule rationale and
classification transparency. ReClass goes a step further by making a historical
classification into a cryptographic receipt: "this exact evidence under this exact
engine/config version produced this exact tier." That is a systems-level answer to
the reproducibility and discordance problems described by ClinVar/ClinGen and
laboratory-concordance studies.

### 4.2 Controlled attribution of failure to evidence completeness

**Claim.** The validation design isolates scoring logic from evidence
availability.

**Project evidence.** The same deterministic engine/config produces 94.7%
definitive concordance on `clingen_real_v1` when fed expert-applied ClinGen
criteria, but only 5.0% on `clinvar_real_v1` when fed sparse public signals
mostly limited to REVEL/frequency. No threshold change explains the difference:
the evidence condition changes.

**Extension beyond literature.** Many tool papers report a single benchmark
metric against ClinVar or eRepo. ReClass's paired design makes a narrower but more
informative finding: the point model can reproduce expert-panel tiers when the
expert evidence is present, and it collapses toward VUS when evidence is sparse.
The bottleneck is evidence recovery and curation, not arithmetic.

### 4.3 Measured lift from cross-source evidence transfer

**Claim.** Direct ClinGen-to-ClinVar evidence enrichment materially improves
concordance and reduces serious errors.

**Project evidence.** In `comparison_clinvar_real_v1_vs_clinvar_enriched_v1.md`,
adding matched ClinGen-applied criteria to 10,649 of 21,638 ClinVar records moved
definitive concordance from 5.0% to 37.8%, overall exact concordance from 19.9%
to 43.3%, and serious discordance count from 34 to 9. Pathogenic recall moved
from 0% to 26.4%; Likely Pathogenic recall from 0% to 46.3%.

**Extension beyond literature.** Harrison et al. showed that evidence sharing can
resolve discordance. ReClass operationalizes that idea in a reproducible pipeline:
same fixture population, same engine, direct-ID ClinGen enrichment, measured
before/after deltas. The remaining failure is also informative: direct Variation
ID matches covered about half of ClinVar cases, and the current ClinGen-derived
fixture has no usable locus index for canonical-key fallback. The next research
lever is source identity and evidence coverage.

### 4.4 Empirical rediscovery of founder/frequency exception failure modes

**Claim.** A naive global BA1 stand-alone benign rule creates serious errors for
known high-carrier pathogenic/founder contexts.

**Project evidence.** `failure_analysis_clingen_real_v1.md` shows four serious
errors in the ClinGen benchmark. Three are Hearing Loss VCEP cases in which BA1
stand-alone benign evidence overrode pathogenic ClinGen evidence: GJB2 c.35delG,
GJB2 c.167delT, and SLC26A4 c.349C>T. The code already supports reviewable
overrides in `engine/configs/base_v1.json`.

**Extension beyond literature.** ClinGen already recognizes BA1 exceptions and
VCEP-specific frequency thresholds. ReClass does not discover the exception as
new biology. What it does accomplish is to reproduce the failure mode from data
using a generic config, quantify its seriousness, and show why overrides must be
versioned, reviewable, and auditable rather than hidden in code.

### 4.5 Provenance-rich evidence bundles as a first-class artifact

**Claim.** ReClass preserves the full evidence bundle that produced a
classification, not merely the final criteria list.

**Project evidence.** `evidence/model.py` defines `EvidenceBundle` with events,
provider versions, source records, warnings, and match metadata. Bundle hashes can
be verified against classification receipts. Providers report match routes and
warnings such as label disagreement, duplicate matches, missing identifiers, or
gnomAD fallback behavior.

**Extension beyond literature.** Many tools emit criteria/rationales. ReClass
treats provider versions and source-record linkage as part of the scientific
object being evaluated. This enables reanalysis when a provider, config, or
evidence source changes and allows the project to distinguish "same tier, changed
evidence" from clinically meaningful tier crossings.

### 4.6 Clinical/research governance integrated with classification

**Claim.** ReClass pairs classification receipts with privacy-preserving storage
boundaries.

**Project evidence.** `db/schema.sql` separates `clinical.*` identified,
tenant-scoped tables from `research.*` de-identified evidence tables. Row-level
security protects clinical rows by tenant. Tests assert tenant isolation, absence
of patient/tenant identifiers in research tables, and no foreign key path from
research back to clinical.

**Extension beyond literature.** Most academic classifiers stop at outputting a
call. ReClass makes tenant isolation, de-identified evidence persistence,
verification, reanalysis, and sign-off part of the same model. That is a systems
contribution, not a new ACMG method.

### 4.7 Offline deterministic validation artifacts

**Claim.** Once fixtures are built, scoring and validation can be rerun without
network calls, clocks, randomness, or live source drift.

**Project evidence.** The scoring core is pure; fixtures and generated validation
reports are committed under `validation/`; diagnostic plots are written under
`plots/`; source governance is documented. Storage/reanalysis tests that need
PostgreSQL skip cleanly when unavailable.

**Extension beyond literature.** This addresses a known reproducibility weakness
in annotation-driven tools: live databases and changing source snapshots can
change results. ReClass makes source refreshes explicit and versioned.

---

## 5. What is not novel

- The ACMG/AMP five-tier framework is not new.
- The Bayesian/point scoring model is Tavtigian/ClinGen SVI, not invented here.
- REVEL PP3/BP4 thresholds come from ClinGen/Pejaver calibration, not from a new
  local calibration.
- Automated ACMG classification already exists in tools such as InterVar,
  CharGer, TAPES, GeneBe, BIAS-2015, VarSome, Franklin, AutoPVS1, and others.
- Concordance with ClinGen or ClinVar is not biological ground truth.
- The project does not automatically read the literature, evaluate functional
  assays, infer segregation, judge phenotype specificity, or clinically validate
  a patient report.
- The current source coverage is narrow, especially outside missense SNVs,
  small variants with coordinates, and cases where ClinGen/REVEL/gnomAD/cohort
  signals are available.

---

## 6. Main research conclusion

The central finding is:

> Deterministic ACMG/AMP point scoring is not the primary blocker. Evidence
> completeness, source identity, provenance, gene/disease-specific rules, and
> auditable reanalysis are the primary blockers.

ReClass supports this conclusion by running the same engine under three evidence
conditions:

1. Expert-applied ClinGen criteria -> high concordance.
2. Sparse ClinVar public signals -> poor concordance.
3. ClinVar plus matched ClinGen evidence -> large but incomplete improvement.

This is a genuine research extension because it changes the question from "can a
rule engine classify variants?" to "which missing evidence and provenance links
prevent a deterministic engine from reproducing expert assertions?"

---

## 7. Open research questions set up by this project

1. **Evidence recovery and identity matching.** How much additional lift appears
   when ClinGen/eRepo source records expose usable loci, canonical-key fallback is
   populated, and reference-backed indel normalization is run against a production
   GRCh38 FASTA?

2. **Conflict resolution.** What deterministic, auditable policy should govern
   conflicts such as BA1/BS1 population evidence versus curated pathogenic
   founder-variant evidence?

3. **VCEP-specific configuration.** Which current VCEP criteria specifications,
   BA1/BS1 thresholds, PS4/PM3 count rules, and local lab policies close the
   remaining serious-error gap without sacrificing reconstructability?

4. **Broader evidence providers.** How should the system incorporate calibrated
   splice, indel, functional, segregation, phenotype, CNV, mitochondrial,
   non-coding, structural-variant, and repeat-expansion evidence while preserving
   versioned receipts?

5. **Phenotype-aware extensions.** The 2026 comparative-tool literature suggests
   phenotype-aware prioritization can outperform purely genomic feature tools.
   What is the right boundary between ReClass as a variant-evidence calculator and
   phenotype-aware case interpretation?

6. **Longitudinal reanalysis governance.** How should a clinical service manage
   evidence/config/source-version changes at cohort scale, distinguishing
   same-tier evidence changes from tier-crossing alerts?

7. **Equity and frequency evidence.** How should ancestry-specific frequency,
   local ancestry, under-sampled populations, and founder effects be represented
   so that BA1/BS1/PM2 reasoning does not overstate certainty?

---

## 8. Key references and sources consulted

### Foundational ACMG/AMP and quantitative frameworks

1. Richards S, Aziz N, Bale S, et al. Standards and guidelines for the
   interpretation of sequence variants. *Genetics in Medicine* 2015.
   https://pubmed.ncbi.nlm.nih.gov/25741868/

2. Tavtigian SV, Greenblatt MS, Harrison SM, et al. Modeling the ACMG/AMP variant
   classification guidelines as a Bayesian classification framework. *Genetics in
   Medicine* 2018. https://pubmed.ncbi.nlm.nih.gov/29300386/

3. Tavtigian SV, Harrison SM, Boucher KM, Biesecker LG. Fitting a naturally
   scaled point system to the ACMG/AMP variant classification guidelines. *Human
   Mutation* 2020. https://pubmed.ncbi.nlm.nih.gov/32720330/

4. ClinGen Variant Classification Guidance. Last updated July 2025.
   https://clinicalgenome.org/tools/clingen-variant-classification-guidance/

5. ClinGen Sequence Variant Interpretation page, archived/retired notice and
   historical SVI resources.
   https://clinicalgenome.org/working-groups/sequence-variant-interpretation/

### Evidence calibration and source resources

6. Pejaver V, Byrne AB, Feng BJ, et al. Calibration of computational tools for
   missense variant pathogenicity classification and ClinGen recommendations for
   PP3/BP4 criteria. *American Journal of Human Genetics* 2022.
   https://pmc.ncbi.nlm.nih.gov/articles/PMC9748256/

7. Bergquist T, Stenton SL, et al. Calibration of additional computational tools
   expands ClinGen recommendation options for PP3/BP4. *Genetics in Medicine*
   2025 / PubMed record. https://pubmed.ncbi.nlm.nih.gov/40084623/

8. Brnich SE, Abou Tayoun AN, Couch FJ, et al. Recommendations for application of
   the functional evidence PS3/BS3 criterion using the ACMG/AMP sequence variant
   interpretation framework. *Genome Medicine* 2019/2020.
   https://clinicalgenome.org/docs/recommendations-for-application-of-the-functional-evidence-ps3-bs3-criterion-using-the-acmg-amp-sequence-variant-interpretation/

9. Ioannidis NM, Rothstein JH, Pejaver V, et al. REVEL: an ensemble method for
   predicting the pathogenicity of rare missense variants. *American Journal of
   Human Genetics* 2016. https://pubmed.ncbi.nlm.nih.gov/27666373/

10. gnomAD v4.1 release notes, including joint allele numbers and discordant
    exome/genome frequency flags. https://gnomad.broadinstitute.org/news/2024-04-gnomad-v4-1/

11. Karczewski KJ, Francioli LC, Tiao G, et al. The mutational constraint spectrum
    quantified from variation in 141,456 humans. *Nature* 2020.
    https://pubmed.ncbi.nlm.nih.gov/32461654/

12. NCBI ClinVar introduction and scope.
    https://www.ncbi.nlm.nih.gov/clinvar/intro/

13. ClinGen Evidence Repository.
    https://erepo.clinicalgenome.org/evrepo/

### Automated and semi-automated tools

14. Li Q, Wang K. InterVar: clinical interpretation of genetic variants by the
    2015 ACMG-AMP guidelines. *American Journal of Human Genetics* 2017.
    https://pubmed.ncbi.nlm.nih.gov/28132688/

15. wInterVar / InterVar web documentation.
    https://wintervar.wglab.org/

16. Scott AD, Huang KL, Weerasinghe A, et al. CharGer: clinical Characterization
    of Germline variants. *Bioinformatics* 2019.
    https://academic.oup.com/bioinformatics/article/35/5/865/5068593

17. Xavier A, Scott RJ, Talseth-Palmer BA. TAPES: a tool for assessment and
    prioritisation in exome studies. *PLOS Computational Biology* 2019.
    https://journals.plos.org/ploscompbiol/article?id=10.1371/journal.pcbi.1007453

18. Stawinski P, Ploski R. GeneBe.net: implementation and validation of an
    automatic ACMG variant pathogenicity criteria assignment. PubMed record.
    https://pubmed.ncbi.nlm.nih.gov/38440907/

19. GeneBe ACMG implementation documentation.
    https://docs.genebe.net/docs/acmg/

20. Eisenhart C, Brickey R, Nadon B, Mewton J, Bayat V. Automating ACMG variant
    classifications with BIAS-2015 v2.1.1: algorithm analysis and benchmark
    against the FDA-approved eRepo dataset. *Genome Medicine* 2025.
    https://link.springer.com/article/10.1186/s13073-025-01581-y

21. Xiang J, Yang J, Chen L, et al. AutoPVS1: an automatic classification tool for
    PVS1 interpretation of null variants. *Human Mutation* 2020.
    https://pubmed.ncbi.nlm.nih.gov/32442321/

22. VarSome germline classifier implementation notes.
    https://varsome.com/about/resources/germline-implementation/

23. OpenCRAVAT calibrated classification package for ACMG/AMP computational
    predictors. https://www.opencravat.org/calibrated-classification-package-applying-acmg-amp-guidelines-to-computational-predictors-in-opencravat/

24. Ghasemnejad T, Liang Y, Jahanian KH, et al. Comprehensive evaluation of
    ACMG/AMP-based variant classification tools. *Bioinformatics* 2026.
    https://academic.oup.com/bioinformatics/article/42/2/btaf623/8483023

### Discordance and reproducibility

25. Harrison SM, Dolinsky JS, Knight Johnson AE, et al. Clinical laboratories
    collaborate to resolve differences in variant interpretations submitted to
    ClinVar. *Genetics in Medicine* 2017.
    https://pubmed.ncbi.nlm.nih.gov/28301460/

26. Amendola LM, Muenzen K, Biesecker LG, et al. Variant classification
    concordance using the ACMG-AMP variant interpretation guidelines across nine
    genomic implementation research studies. *American Journal of Human Genetics*
    2020. https://pubmed.ncbi.nlm.nih.gov/33108757/

27. ClinGen Variant Curation Interface paper and ERepo context.
    https://pmc.ncbi.nlm.nih.gov/articles/PMC8764818/

---

## 9. Internal cross-references

- Project overview: `overview.md`
- Current technical status: `ReClass Model/README.md`
- Honest model boundaries: `limitations.md`
- Unfinished work: `gap.md`
- Validation reports: `ReClass Model/validation/reports/`
- Data governance: `ReClass Model/docs/data_governance.md`
