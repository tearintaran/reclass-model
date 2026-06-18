"""Continuous reanalysis + cohort-PS4 support (gap §8).

Two concerns live here, both kept *out* of ``monitoring/diff.py`` so that module's
public API (``Alert``, ``diff``, ``is_serious_crossing``) stays stable for the
shared engine tests:

  * **Cohort PS4** — turn de-identified ``research.cohort_counts`` into a single
    standardized PS4 ``EvidenceEvent`` (case-control enrichment), with thresholds
    that can be overridden per gene/disease after clinical governance review. These
    are pure functions of the counts + rules (no DB, no engine mutation).

  * **Reanalysis orchestration** — recompute a variant's classification from current
    evidence and persist a new receipt **only when the result actually changes**
    (no churn). A tier crossing additionally writes a ``clinical.alert``; a same-tier
    point change is recorded in ``clinical.reanalysis_event`` but pages no one.

Storage is imported lazily inside :func:`reanalyze` so the pure PS4 helpers (and
their tests) import cleanly even where psycopg/PostgreSQL is unavailable.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple, Union

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
        published case-control study is unavailable, some ClinGen VCEPs count
        unrelated affected probands carrying the variant and escalate strength by
        that count. These rules set ``min_enrichment = 1.0`` (any positive proband
        count clears the ratio gate) and carry the VCEP's proband thresholds in
        ``supporting_cases`` / ``moderate_cases`` / ``strong_cases``.

    The applied strength escalates by observed case (proband) count.

    IMPORTANT (clinical use): VCEP proband-counting specifications additionally
    require the variant to meet PM2 (absent/rare in the population). That
    prerequisite is *not* re-checked here — it is supplied as a separate PM2
    ``EvidenceEvent`` that the engine sums independently. These thresholds encode
    reviewed VCEP specifications but, like ``engine.config``, must be signed off
    locally before clinical use; the values resolve nothing biological on their own.
    """

    min_cases: int = 5
    min_enrichment: float = 5.0
    supporting_cases: int = 5
    moderate_cases: int = 10
    strong_cases: int = 20


@dataclass(frozen=True)
class PS4OddsRatioRule:
    """PS4 from a case/control **odds ratio** and its 95% CI lower bound.

    The ClinGen Cardiomyopathy gene CSpecs (e.g. MYH7, MYBPC3, ACTC1 v1.0.0) do not
    apply PS4 from a raw proband count: they require a case-control **odds ratio**
    whose 95% confidence-interval *lower bound* clears a threshold, so a numerically
    large but statistically uncertain enrichment cannot reach PS4. This rule encodes
    that mode. Unlike :class:`PS4Rule` it needs **denominators** -- the total number
    of cases and controls screened -- so a 2x2 table can be built; cohort rows that
    carry only ``case_count`` / ``control_count`` (variant-positive counts) without
    ``case_total`` / ``control_total`` cannot be evaluated and yield no event, which
    is why a cardiomyopathy gene with bare proband counts contributes nothing.

    The odds ratio and its CI are computed with a Haldane-Anscombe (+0.5 per cell)
    continuity correction when any cell is empty, exactly the standard small-sample
    correction; the math is pure and deterministic (no randomness, no wall clock).

    ``ci_lower_to_strength`` bins map the 95% CI lower bound -> strength, evaluated
    high threshold first. ``significance_floor`` is the lower-bound value the CI must
    *strictly exceed* before any PS4 applies (1.0 = "the interval excludes OR=1", the
    usual significance gate). ``min_variant_cases`` requires a minimum number of
    variant-positive cases so a single observation cannot drive a strong call.

    Like :data:`PS4Rule`, these thresholds are reviewable governance defaults: confirm
    them against the current ClinGen Cardiomyopathy CSpec before clinical use.
    """

    ci_lower_to_strength: Tuple[Tuple[float, str], ...] = (
        (5.0, "strong"), (3.0, "moderate"), (1.5, "supporting"),
    )
    significance_floor: float = 1.0
    min_variant_cases: int = 4
    z: float = 1.959963984540054  # 1.96 -> two-sided 95% CI (stdlib-free constant)


# Clinical-governance note for the rules encoded below.
PS4_RULE_REVIEW = {
    "review_date": "2026-06-16",
    "review_status": "governance_reviewed_pending_credentialed_signoff",
    "hearing_loss_cspec": (
        "ClinGen Hearing Loss Expert Panel Specifications v2.0.0, released "
        "2022-03-30, CSpec https://cspec.genome.network/cspec/ui/svi/doc/GN005; "
        "proband-count PS4 text is autosomal-dominant-specific"
    ),
    "cardiomyopathy_cspec": (
        "Current ClinGen Cardiomyopathy gene CSpecs, including ACTC1 v1.0.0 "
        "released 2024-04-22, use PS4 odds-ratio 95% CI lower-bound thresholds "
        "rather than the historical simple proband-count shortcut. Implemented via "
        "CARDIOMYOPATHY_OR_RULE / PS4OddsRatioRule (case-control OR with a Wald 95% "
        "CI); the CI lower-bound -> strength bins are reviewable defaults pending "
        "credentialed sign-off and must be confirmed against the current gene CSpec."
    ),
    "clinical_release": "blocked_until_credentialed_human_signoff",
}

# Generic case-control default for genes/diseases with no VCEP-specific rule, or for
# VCEPs whose current specifications require statistics this helper cannot compute
# from plain proband counts (for example cardiomyopathy OR 95% CI lower bounds).
# Conservative, auditable thresholds in the spirit of ACMG/AMP 2015 PS4 ("prevalence
# in affecteds statistically increased over controls", Richards et al. 2015) and the
# ClinGen SVI Bayesian points framework (Tavtigian et al. 2020): require a non-trivial
# affected count plus a clear case/control enrichment before applying any strength.
DEFAULT_PS4_RULE = PS4Rule(
    min_cases=5, min_enrichment=5.0,
    supporting_cases=5, moderate_cases=10, strong_cases=20,
)

# Proband-count PS4 specification confirmed in the current ClinGen Hearing Loss VCEP
# v2.0.0 CSpec: for autosomal dominant hearing-loss interpretations, >=2 / >=6 /
# >=15 unrelated probands -> PS4_Supporting / PS4_Moderate / PS4_Strong, applicable
# only when PM2_Supporting is met. ``min_enrichment = 1.0`` because this VCEP counts
# probands rather than requiring a control cohort.
PROBAND_COUNT_AD_RULE = PS4Rule(
    min_cases=2, min_enrichment=1.0,
    supporting_cases=2, moderate_cases=6, strong_cases=15,
)

# Current ClinGen Cardiomyopathy CSpecs apply PS4 from a case/control odds ratio
# whose 95% CI lower bound clears a threshold (NOT a simple proband count). These
# genes are registered with CARDIOMYOPATHY_OR_RULE: given case/control denominators
# the OR and its CI are computed and mapped to PS4 strength; given only bare proband
# counts (no denominators) no event fires, so a numerically large but statistically
# unsupported enrichment never reaches PS4.
_CARDIOMYOPATHY_CASE_CONTROL_ONLY_GENES = (
    "MYH7", "MYBPC3", "TNNT2", "TNNI3", "TPM1", "ACTC1", "MYL2", "MYL3",
)
# Reviewable cardiomyopathy PS4 OR thresholds (95% CI lower bound -> strength). These
# encode the ClinGen Cardiomyopathy CSpec mode (case-control OR with CI), pending the
# credentialed sign-off recorded in PS4_RULE_REVIEW; the exact bin values are a
# clinical decision and must be confirmed against the current gene CSpec.
CARDIOMYOPATHY_OR_RULE = PS4OddsRatioRule(
    ci_lower_to_strength=((5.0, "strong"), (3.0, "moderate"), (1.5, "supporting")),
    significance_floor=1.0,
    min_variant_cases=4,
)
# ClinGen Hearing Loss VCEP genes in v2.0.0 where the CSpec lists autosomal dominant
# nonsyndromic hearing-loss interpretation and the proband-count PS4 text can be
# represented by this count-only helper. Recessive genes such as GJB2/SLC26A4/CDH23/
# USH2A intentionally fall back to the default because the CSpec's proband-count
# text is autosomal-dominant-specific.
_HEARING_LOSS_AD_PROBAND_GENES = (
    "COCH", "KCNQ4", "MYO6",
)

# Per gene/disease overrides. Keyed by (GENE, disease); ``disease=None`` is a
# gene-wide default. Lookups fall back gene-wide, then to DEFAULT_PS4_RULE.
PS4RuleType = Union[PS4Rule, PS4OddsRatioRule]
PS4_RULES: Dict[Tuple[Optional[str], Optional[str]], PS4RuleType] = {}
for _gene in _HEARING_LOSS_AD_PROBAND_GENES:
    PS4_RULES[(_gene, None)] = PROBAND_COUNT_AD_RULE
for _gene in _CARDIOMYOPATHY_CASE_CONTROL_ONLY_GENES:
    PS4_RULES[(_gene, None)] = CARDIOMYOPATHY_OR_RULE


def resolve_ps4_rule(gene: Optional[str] = None,
                     disease: Optional[str] = None) -> PS4RuleType:
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


def odds_ratio_ci(
    variant_cases: int,
    noncarrier_cases: int,
    variant_controls: int,
    noncarrier_controls: int,
    *,
    z: float = 1.959963984540054,
) -> Tuple[float, float, float]:
    """Case-control odds ratio with a Wald 95% CI ``(or, ci_lower, ci_upper)``.

    The 2x2 table is ``[[variant_cases, noncarrier_cases], [variant_controls,
    noncarrier_controls]]``. A Haldane-Anscombe (+0.5 per cell) continuity
    correction is applied whenever any cell is empty, the standard small-sample fix
    that keeps the OR and its log-scale standard error finite. Pure and
    deterministic: ``OR = (a*d)/(b*c)``, ``SE(ln OR) = sqrt(1/a+1/b+1/c+1/d)``,
    ``CI = exp(ln OR +/- z*SE)`` (Bland & Altman 2000).
    """
    a = float(variant_cases)
    b = float(noncarrier_cases)
    c = float(variant_controls)
    d = float(noncarrier_controls)
    if min(a, b, c, d) <= 0:                     # Haldane-Anscombe correction
        a, b, c, d = a + 0.5, b + 0.5, c + 0.5, d + 0.5
    odds_ratio = (a * d) / (b * c)
    ln_or = math.log(odds_ratio)
    se = math.sqrt(1.0 / a + 1.0 / b + 1.0 / c + 1.0 / d)
    return odds_ratio, math.exp(ln_or - z * se), math.exp(ln_or + z * se)


def _ps4_or_strength(ci_lower: float, rule: PS4OddsRatioRule) -> Optional[str]:
    """Map a 95% CI lower bound to a PS4 strength (or None below the floor)."""
    if ci_lower <= rule.significance_floor:      # interval includes OR=1 -> not significant
        return None
    for threshold, strength in rule.ci_lower_to_strength:  # high -> low
        if ci_lower >= float(threshold):
            return strength
    return None


def _odds_ratio_ps4_event(
    counts: List[Dict[str, Any]],
    rule: PS4OddsRatioRule,
    *,
    gene: Optional[str],
    disease: Optional[str],
    source_version: str,
) -> Optional[EvidenceEvent]:
    """PS4 from a case-control odds ratio (cardiomyopathy CSpec mode).

    Needs denominators: each row must carry ``case_total`` / ``control_total`` (the
    number of cases/controls screened) alongside the variant-positive ``case_count``
    / ``control_count``. Without denominators the 2x2 table is undefined and no event
    fires -- so a cardiomyopathy variant reported only as a bare proband count
    contributes nothing until proper case-control data is supplied.
    """
    if not counts or any(
        c.get("case_total") is None or c.get("control_total") is None for c in counts
    ):
        return None
    variant_cases = sum(int(c["case_count"]) for c in counts)
    variant_controls = sum(int(c["control_count"]) for c in counts)
    total_cases = sum(int(c["case_total"]) for c in counts)
    total_controls = sum(int(c["control_total"]) for c in counts)
    noncarrier_cases = total_cases - variant_cases
    noncarrier_controls = total_controls - variant_controls
    # Counts must be internally consistent and clear the minimum-observation gate.
    if min(noncarrier_cases, noncarrier_controls) < 0 or total_cases <= 0 or total_controls <= 0:
        return None
    if variant_cases < rule.min_variant_cases:
        return None

    odds_ratio, ci_lower, ci_upper = odds_ratio_ci(
        variant_cases, noncarrier_cases, variant_controls, noncarrier_controls, z=rule.z
    )
    strength = _ps4_or_strength(ci_lower, rule)
    if strength is None:
        return None
    return EvidenceEvent(
        source="cohort",
        acmg_criterion="PS4",
        evidence_direction="pathogenic",
        applied_strength=strength,
        source_version=source_version,
        raw={
            "mode": "odds_ratio",
            "variant_cases": variant_cases,
            "variant_controls": variant_controls,
            "total_cases": total_cases,
            "total_controls": total_controls,
            "odds_ratio": round(odds_ratio, 4),
            "ci_lower": round(ci_lower, 4),
            "ci_upper": round(ci_upper, 4),
            "gene": gene,
            "disease": disease,
        },
    )


def cohort_to_ps4_event(
    counts: List[Dict[str, Any]],
    *,
    gene: Optional[str] = None,
    disease: Optional[str] = None,
    rule: Optional[PS4RuleType] = None,
    source_version: str = "cohort",
) -> Optional[EvidenceEvent]:
    """Aggregate cohort counts into a single PS4 ``EvidenceEvent`` (or ``None``).

    ``counts`` is a list of ``{case_count, control_count, ...}`` rows (e.g. from
    ``storage.evidence.get_cohort_counts``); they are summed across ancestries.
    Returns ``None`` when the applicable rule's gates are not met, so a sparse or
    non-enriched cohort contributes no evidence (rather than a spurious zero).

    Genes governed by a :class:`PS4OddsRatioRule` (the ClinGen Cardiomyopathy CSpec
    mode) instead require case/control denominators and are routed through the
    odds-ratio 95% CI path; bare proband counts yield no event for those genes.
    """
    rule = rule or resolve_ps4_rule(gene, disease)
    if isinstance(rule, PS4OddsRatioRule):
        return _odds_ratio_ps4_event(
            counts, rule, gene=gene, disease=disease, source_version=source_version
        )
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
