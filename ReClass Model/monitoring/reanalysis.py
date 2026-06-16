"""Continuous reanalysis + cohort-PS4 support (gap §8).

Two concerns live here, both kept *out* of ``monitoring/diff.py`` so that module's
public API (``Alert``, ``diff``, ``is_serious_crossing``) stays stable for the
shared engine tests:

  * **Cohort PS4** — turn de-identified ``research.cohort_counts`` into a single
    standardized PS4 ``EvidenceEvent`` (case-control enrichment), with placeholder
    thresholds that can be overridden per gene/disease. These are pure functions of
    the counts + rules (no DB, no engine mutation).

  * **Reanalysis orchestration** — recompute a variant's classification from current
    evidence and persist a new receipt **only when the result actually changes**
    (no churn). A tier crossing additionally writes a ``clinical.alert``; a same-tier
    point change is recorded in ``clinical.reanalysis_event`` but pages no one.

Storage is imported lazily inside :func:`reanalyze` so the pure PS4 helpers (and
their tests) import cleanly even where psycopg/PostgreSQL is unavailable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from engine import config as C
from engine.scoring import EvidenceEvent, classify


# --------------------------------------------------------------------------- #
# Cohort PS4 (case-control enrichment)                                        #
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class PS4Rule:
    """Thresholds for applying the PS4 criterion from cohort case/control counts.

    PS4 (ACMG/AMP) applies when a variant is significantly more prevalent in
    affected cases than controls. Two well-established review modes are supported by
    this one rule shape:

      * **Case-control enrichment** (the generic :data:`DEFAULT_PS4_RULE`): a PS4
        event fires only when both gates pass — at least ``min_cases`` affected
        observations AND a case/control enrichment ratio >= ``min_enrichment``
        (controls floored at 1 so a control-free cohort is still evaluable).
      * **Proband counting** (the per-VCEP overrides in :data:`PS4_RULES`): when a
        published case-control study is unavailable, several ClinGen VCEPs count
        unrelated affected probands carrying the variant and escalate strength by
        that count. These rules set ``min_enrichment = 1.0`` (any positive proband
        count clears the ratio gate) and carry the VCEP's proband thresholds in
        ``supporting_cases`` / ``moderate_cases`` / ``strong_cases``.

    The applied strength escalates by observed case (proband) count.

    IMPORTANT (clinical use): VCEP proband-counting specifications additionally
    require the variant to meet PM2 (absent/rare in the population). That
    prerequisite is *not* re-checked here — it is supplied as a separate PM2
    ``EvidenceEvent`` that the engine sums independently. These thresholds encode
    published VCEP specifications but, like ``engine.config``, must be confirmed
    against the current specification version and signed off locally before clinical
    use; the values resolve nothing biological on their own.
    """

    min_cases: int = 5
    min_enrichment: float = 5.0
    supporting_cases: int = 5
    moderate_cases: int = 10
    strong_cases: int = 20


# Generic case-control default for genes/diseases with no VCEP-specific rule.
# Conservative, auditable thresholds in the spirit of ACMG/AMP 2015 PS4 ("prevalence
# in affecteds statistically increased over controls", Richards et al. 2015) and the
# ClinGen SVI Bayesian points framework (Tavtigian et al. 2020): require a non-trivial
# affected count plus a clear case/control enrichment before applying any strength.
DEFAULT_PS4_RULE = PS4Rule(
    min_cases=5, min_enrichment=5.0,
    supporting_cases=5, moderate_cases=10, strong_cases=20,
)

# Proband-count PS4 specification shared by several ClinGen VCEPs: >=2 / >=6 / >=15
# unrelated probands -> PS4_Supporting / PS4_Moderate / PS4_Strong, applicable only
# when PM2 is met. Originated with the ClinGen Cardiomyopathy Expert Panel
# (Kelly et al., Genet Med 2018) and adopted unchanged by the Hearing Loss VCEP
# (Oza et al., Hum Mutat 2018). ``min_enrichment = 1.0`` because these VCEPs count
# probands rather than requiring a control cohort.
PROBAND_COUNT_AD_RULE = PS4Rule(
    min_cases=2, min_enrichment=1.0,
    supporting_cases=2, moderate_cases=6, strong_cases=15,
)

# ClinGen Cardiomyopathy Expert Panel definitively curated genes (Kelly et al. 2018).
_CARDIOMYOPATHY_GENES = (
    "MYH7", "MYBPC3", "TNNT2", "TNNI3", "TPM1", "ACTC1", "MYL2", "MYL3",
)
# ClinGen Hearing Loss VCEP genes that adopted the same proband-count PS4 spec
# (Oza et al. 2018; gene list per the v2 specification).
_HEARING_LOSS_GENES = (
    "GJB2", "SLC26A4", "MYO7A", "MYO6", "CDH23", "TECTA", "KCNQ4", "COCH", "USH2A",
)

# Per gene/disease overrides. Keyed by (GENE, disease); ``disease=None`` is a
# gene-wide default. Lookups fall back gene-wide, then to DEFAULT_PS4_RULE.
PS4_RULES: Dict[Tuple[Optional[str], Optional[str]], PS4Rule] = {}
for _gene in _CARDIOMYOPATHY_GENES + _HEARING_LOSS_GENES:
    PS4_RULES[(_gene, None)] = PROBAND_COUNT_AD_RULE


def resolve_ps4_rule(gene: Optional[str] = None,
                     disease: Optional[str] = None) -> PS4Rule:
    """Pick the most specific PS4 rule for a gene/disease (else the default)."""
    g = gene.upper() if gene else None
    for key in ((g, disease), (g, None)):
        if key in PS4_RULES:
            return PS4_RULES[key]
    return DEFAULT_PS4_RULE


def _ps4_strength(cases: int, rule: PS4Rule) -> Optional[str]:
    if cases >= rule.strong_cases:
        return "strong"
    if cases >= rule.moderate_cases:
        return "moderate"
    if cases >= rule.supporting_cases:
        return "supporting"
    return None


def cohort_to_ps4_event(
    counts: List[Dict[str, Any]],
    *,
    gene: Optional[str] = None,
    disease: Optional[str] = None,
    rule: Optional[PS4Rule] = None,
    source_version: str = "cohort",
) -> Optional[EvidenceEvent]:
    """Aggregate cohort counts into a single PS4 ``EvidenceEvent`` (or ``None``).

    ``counts`` is a list of ``{case_count, control_count, ...}`` rows (e.g. from
    ``storage.evidence.get_cohort_counts``); they are summed across ancestries.
    Returns ``None`` when the enrichment/case-count gates are not met, so a sparse
    or non-enriched cohort contributes no evidence (rather than a spurious zero).
    """
    rule = rule or resolve_ps4_rule(gene, disease)
    cases = sum(int(c["case_count"]) for c in counts)
    controls = sum(int(c["control_count"]) for c in counts)
    if cases < rule.min_cases:
        return None
    enrichment = cases / max(controls, 1)
    if enrichment < rule.min_enrichment:
        return None
    strength = _ps4_strength(cases, rule)
    if strength is None:
        return None
    return EvidenceEvent(
        source="cohort",
        acmg_criterion="PS4",
        evidence_direction="pathogenic",
        applied_strength=strength,
        source_version=source_version,
        raw={
            "cases": cases,
            "controls": controls,
            "enrichment": round(enrichment, 4),
            "gene": gene,
            "disease": disease,
        },
    )


def events_with_cohort_ps4(
    base_events: List[EvidenceEvent],
    counts: List[Dict[str, Any]],
    *,
    gene: Optional[str] = None,
    disease: Optional[str] = None,
) -> List[EvidenceEvent]:
    """Return ``base_events`` plus a cohort-derived PS4 event when one applies."""
    ps4 = cohort_to_ps4_event(counts, gene=gene, disease=disease)
    return base_events + [ps4] if ps4 is not None else list(base_events)


# --------------------------------------------------------------------------- #
# Reanalysis orchestration                                                    #
# --------------------------------------------------------------------------- #
@dataclass
class ReanalysisResult:
    """Outcome of one reanalysis run for a (tenant, variant)."""

    changed: bool
    crossed: bool
    old_tier: Optional[str]
    new_tier: str
    old_points: Optional[float]
    new_points: float
    new_classification_id: Optional[str] = None
    reanalysis_id: Optional[str] = None
    alert_id: Optional[str] = None


def reanalyze(
    cur,
    *,
    tenant_id: str,
    variant_id: str,
    new_events: List[EvidenceEvent],
    engine_version: str = C.ENGINE_VERSION,
    trigger: str = "evidence",
    patient_id: Optional[str] = None,
    prior: Optional[Dict[str, Any]] = None,
    persist: bool = True,
    prior_bundle_id: Optional[str] = None,
    new_bundle_id: Optional[str] = None,
) -> ReanalysisResult:
    """Recompute a variant's classification and persist/alert only on real change.

    Churn guard: if the recomputed result is byte-for-byte identical to the prior
    receipt (same ``reconstruction_hash``), nothing is written and
    ``changed=False`` is returned. Otherwise a new ``clinical.classification``
    receipt is persisted; a tier crossing also writes a ``clinical.alert`` and a
    same-tier change is recorded in ``clinical.reanalysis_event`` without paging.

    ``prior_bundle_id`` / ``new_bundle_id`` (optional) record the de-identified
    ``research.evidence_bundle`` receipts behind the old and new classifications on
    the audit row, so a reviewer can reconstruct the exact evidence delta that drove
    a tier change (gap §5 task 3).

    Must run on a tenant-scoped session. Set ``persist=False`` for a dry run (no
    writes) that still reports whether the result changed/crossed.
    """
    from storage import classifications as cls  # lazy: keeps PS4 helpers psycopg-free
    from storage import alerts as al

    if prior is None:
        existing = cls.list_classifications(cur, variant_id=variant_id)
        prior = existing[-1] if existing else None

    new_clf = classify(new_events, engine_version=engine_version)

    old_tier = prior["tier"] if prior else None
    old_points = float(prior["total_points"]) if prior else None
    prior_id = str(prior["classification_id"]) if prior else None

    # No churn: an identical recomputed result writes nothing. The reconstruction
    # hash already incorporates the engine version, so equal hash == equal result.
    if prior is not None and new_clf.reconstruction_hash == prior["reconstruction_hash"]:
        return ReanalysisResult(
            changed=False, crossed=False, old_tier=old_tier,
            new_tier=new_clf.tier, old_points=old_points,
            new_points=new_clf.total_points,
        )

    crossed = old_tier is not None and old_tier != new_clf.tier

    if not persist:
        return ReanalysisResult(
            changed=True, crossed=bool(crossed), old_tier=old_tier,
            new_tier=new_clf.tier, old_points=old_points,
            new_points=new_clf.total_points,
        )

    new_id = cls.insert_classification(
        cur, tenant_id=tenant_id, variant_id=variant_id,
        classification=new_clf, patient_id=patient_id,
    )

    # First-ever classification: nothing to compare, so no alert / audit row.
    if prior is None:
        return ReanalysisResult(
            changed=True, crossed=False, old_tier=None, new_tier=new_clf.tier,
            old_points=None, new_points=new_clf.total_points,
            new_classification_id=new_id,
        )

    alert_id = None
    if crossed:
        alert_id = al.record_rescoring(
            cur, tenant_id=tenant_id, variant_id=variant_id,
            old_tier=old_tier, new_tier=new_clf.tier,
        )
    reanalysis_id = al.record_reanalysis_event(
        cur, tenant_id=tenant_id, variant_id=variant_id,
        old_tier=old_tier, new_tier=new_clf.tier,
        old_points=old_points, new_points=new_clf.total_points,
        new_classification_id=new_id, prior_classification_id=prior_id,
        trigger=trigger, alert_id=alert_id,
        prior_bundle_id=prior_bundle_id, new_bundle_id=new_bundle_id,
    )
    return ReanalysisResult(
        changed=True, crossed=crossed, old_tier=old_tier, new_tier=new_clf.tier,
        old_points=old_points, new_points=new_clf.total_points,
        new_classification_id=new_id, reanalysis_id=reanalysis_id, alert_id=alert_id,
    )
