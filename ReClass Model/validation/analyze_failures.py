#!/usr/bin/env python3
"""Failure-analysis triage for the ACMG/AMP reclassification engine.

Turns a validation report into an actionable, ranked triage of *why* the engine
got cases wrong, so failures stop requiring manual JSON reading.

Stdlib-only CLI. Run from the ``ReClass Model/`` directory:

    PY="../.venv/bin/python"
    $PY validation/analyze_failures.py clinvar_real_v1
    $PY validation/analyze_failures.py clingen_real_v1

It resolves the report JSON by benchmark name, joins the matching fixture by case
``id``, summarizes every mismatch (``match == false``), prints a detail block for
every serious error (``serious == true``), and writes a compact triage report to
``validation/reports/failure_analysis_<name>.md`` plus a ``.json`` sibling with
the structured rollups.

The analysis core (:func:`analyze`, :func:`classify_gap`, :func:`tier_rank`, ...)
takes plain dicts and has no filesystem dependency, so it is unit-testable with
tiny in-memory report/fixture pairs.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.scoring import classify_signals  # noqa: E402
from engine import config as C  # noqa: E402
from reporting.reviewer import build_validation_review_packet  # noqa: E402

CLINICAL_RELEASE_STATE = "governance_reviewed_pending_credentialed_signoff"
CLINICAL_RELEASE_STATEMENT = (
    "Clinical-release state: governance_reviewed_pending_credentialed_signoff; "
    "this report is not credentialed clinical sign-off."
)

# --------------------------------------------------------------------------- #
# Tier model
# --------------------------------------------------------------------------- #

# Pathogenic (high) -> Benign (low). Used to decide the direction of a mismatch.
TIER_RANK = {
    "Pathogenic": 4,
    "Likely Pathogenic": 3,
    "VUS": 2,
    "Likely Benign": 1,
    "Benign": 0,
}


def tier_rank(tier):
    """Numeric rank for an ACMG tier; unknown tiers sort as VUS-neutral (2)."""
    return TIER_RANK.get(tier, 2)


# Stable evidence-gap categories (keys group the ranked gap list cleanly; the
# text is the human-readable "what to fix next" guidance).
GAP_TEXT = {
    "missing_pathogenic_criteria": (
        "no pathogenic criteria supplied (only computational/frequency signals); "
        "missing PVS1/PS3/PM3-class evidence"
    ),
    "insufficient_pathogenic_strength": (
        "pathogenic criteria present but below the tier threshold; "
        "needs a strength upgrade (PVS1/PS3-class)"
    ),
    "missing_benign_criteria": (
        "no benign criteria supplied (only computational/frequency signals); "
        "missing BA1/BS-class evidence"
    ),
    "insufficient_benign_strength": (
        "benign criteria present but below the tier threshold; "
        "needs a strength downgrade (BA1/BS1-class)"
    ),
    "lateral_tier_disagreement": (
        "same-rank tier disagreement; review tier-cutoff edge case"
    ),
}


# Candidate evidence sources the engine could integrate next, keyed for stable
# ranking. The label is the "what to build" guidance surfaced in the report.
EVIDENCE_SOURCES = {
    "clingen_criteria": (
        "ClinGen / functional ACMG criteria (PVS1/PS3/PM3-class)"
    ),
    "revel": "REVEL computational evidence (PP3/BP4)",
    "gnomad_af": "gnomAD allele frequency (PM2/BA1/BS1)",
}


def recommend_evidence_sources(mismatch_evidence):
    """Rank the next evidence source to implement by expected impact.

    ``mismatch_evidence`` is a list of per-mismatch dicts with keys
    ``category`` (a :data:`GAP_TEXT` key), ``serious`` (bool), ``has_revel``
    (bool), and ``has_gnomad_af`` (bool). A source "could help" a mismatch when:

    * ClinGen/functional criteria: the mismatch is a ``missing_*_criteria`` gap
      (the engine had no direction-appropriate criteria at all).
    * REVEL: the case currently lacks a REVEL score (a computational PP3/BP4
      signal could be added).
    * gnomAD AF: the case currently lacks an allele frequency (a PM2/BA1/BS1
      frequency signal could be added).

    A single mismatch can support several candidate sources. Returns a list of
    ``{source, label, count, serious}`` ranked by count desc, then serious desc.
    """
    agg = {k: {"count": 0, "serious": 0} for k in EVIDENCE_SOURCES}

    def _bump(key, is_serious):
        agg[key]["count"] += 1
        if is_serious:
            agg[key]["serious"] += 1

    for m in mismatch_evidence:
        is_serious = bool(m.get("serious"))
        if m.get("category") in (
            "missing_pathogenic_criteria",
            "missing_benign_criteria",
        ):
            _bump("clingen_criteria", is_serious)
        if not m.get("has_revel"):
            _bump("revel", is_serious)
        if not m.get("has_gnomad_af"):
            _bump("gnomad_af", is_serious)

    ranked = [
        {
            "source": key,
            "label": EVIDENCE_SOURCES[key],
            "count": agg[key]["count"],
            "serious": agg[key]["serious"],
        }
        for key in EVIDENCE_SOURCES
    ]
    ranked.sort(key=lambda x: (-x["count"], -x["serious"], x["source"]))
    return ranked


# --------------------------------------------------------------------------- #
# Signal / criteria helpers
# --------------------------------------------------------------------------- #


def _criteria(fixture_case):
    """Return the supplied criteria list for a fixture case (possibly empty)."""
    return (fixture_case or {}).get("signals", {}).get("criteria", []) or []


def _signals(fixture_case):
    return (fixture_case or {}).get("signals", {}) or {}


def _direction_is(criterion, direction):
    """True if a criterion dict is in ``direction`` (uses the field, else prefix)."""
    d = criterion.get("direction")
    if d:
        return d == direction
    name = str(criterion.get("criterion", "")).upper()
    if not name:
        return False
    # ACMG naming: pathogenic codes start with P (PVS1/PS#/PM#/PP#), benign with B.
    return name[0] == ("P" if direction == "pathogenic" else "B")


def has_pathogenic_criteria(criteria):
    return any(_direction_is(c, "pathogenic") for c in criteria)


def has_benign_criteria(criteria):
    return any(_direction_is(c, "benign") for c in criteria)


def signals_present(signals):
    """Short list of which non-criteria signals are present, e.g. ['REVEL']."""
    present = []
    if signals.get("revel") is not None:
        present.append("REVEL")
    if signals.get("gnomad_af") is not None:
        present.append("gnomAD AF")
    return present


def criteria_count_bucket(n):
    if n == 0:
        return "0"
    if n <= 2:
        return "1-2"
    if n <= 4:
        return "3-4"
    return "5+"


def classify_gap(expected, predicted, criteria, signals):
    """Infer (direction, gap_category) for a single mismatch.

    direction: 'under-pathogenic' (engine too benign), 'over-pathogenic'
    (engine too pathogenic), or 'lateral' (same rank, different tier label).
    gap_category is a key into :data:`GAP_TEXT`.
    """
    er, pr = tier_rank(expected), tier_rank(predicted)
    if er > pr:
        # Engine landed too benign -> it lacks pathogenic evidence.
        if has_pathogenic_criteria(criteria):
            return "under-pathogenic", "insufficient_pathogenic_strength"
        return "under-pathogenic", "missing_pathogenic_criteria"
    if er < pr:
        # Engine landed too pathogenic -> it lacks benign evidence.
        if has_benign_criteria(criteria):
            return "over-pathogenic", "insufficient_benign_strength"
        return "over-pathogenic", "missing_benign_criteria"
    return "lateral", "lateral_tier_disagreement"


# --------------------------------------------------------------------------- #
# Core analysis (pure: dicts in, dict out)
# --------------------------------------------------------------------------- #


def _provenance_link(provenance):
    """Best-effort ClinVar URL from a provenance block, else None."""
    if not provenance:
        return None
    cid = provenance.get("clinvar_id") or provenance.get("variation_id")
    if cid:
        return "https://www.ncbi.nlm.nih.gov/clinvar/variation/%s/" % cid
    return None


def _criteria_summary(criteria):
    """Compact 'PVS1(very_strong), PM3(moderate)' rendering."""
    parts = []
    for c in criteria:
        name = c.get("criterion", "?")
        strength = c.get("strength")
        parts.append("%s(%s)" % (name, strength) if strength else str(name))
    return parts


def criteria_rows(criteria):
    """Full supplied-criteria rows with source/version provenance."""
    rows = []
    for c in criteria or []:
        rows.append({
            "criterion": c.get("criterion", "?"),
            "direction": c.get("direction") or _direction_from_name(c.get("criterion")),
            "strength": c.get("strength"),
            "source": c.get("source", "curated"),
            "source_version": c.get("version") or c.get("source_version"),
            "raw": c.get("raw", {}),
        })
    return rows


def _direction_from_name(name):
    name = str(name or "").upper()
    if not name:
        return None
    return "pathogenic" if name[0] == "P" else "benign" if name[0] == "B" else None


def _classification_detail(signals):
    """Classify fixture signals once and expose contribution arithmetic."""
    try:
        cls = classify_signals(signals or {})
    except Exception as exc:  # pragma: no cover - defensive reporting path
        return {
            "total_points": None,
            "tier": None,
            "contributions": [],
            "overrides": [],
            "reconstruction_hash": None,
            "error": str(exc),
        }
    return {
        "total_points": cls.total_points,
        "tier": cls.tier,
        "contributions": [
            {
                "criterion": c.acmg_criterion,
                "direction": c.evidence_direction,
                "strength": c.applied_strength,
                "source": c.source,
                "source_version": c.source_version,
                "points": c.points,
            }
            for c in cls.contributions
        ],
        "overrides": list(cls.overrides),
        "reconstruction_hash": cls.reconstruction_hash,
        "error": None,
    }


def _has_standalone_benign(criteria):
    return any(
        str(c.get("criterion", "")).upper() == "BA1"
        or str(c.get("strength", "")).lower() == "stand_alone"
        for c in criteria or []
        if _direction_is(c, "benign")
    )


def _has_opposing_evidence(expected, criteria):
    if tier_rank(expected) >= tier_rank("Likely Pathogenic"):
        return has_benign_criteria(criteria)
    if tier_rank(expected) <= tier_rank("Likely Benign"):
        return has_pathogenic_criteria(criteria)
    return has_pathogenic_criteria(criteria) and has_benign_criteria(criteria)


def _has_direction_evidence(expected, criteria):
    if tier_rank(expected) >= tier_rank("Likely Pathogenic"):
        return has_pathogenic_criteria(criteria)
    if tier_rank(expected) <= tier_rank("Likely Benign"):
        return has_benign_criteria(criteria)
    return bool(criteria)


def _label_disagreement_hint(fixture_case):
    enrichment = (fixture_case or {}).get("enrichment") or {}
    warnings = [str(w).lower() for w in enrichment.get("warnings") or []]
    if enrichment.get("label_disagreement"):
        return True
    return any("label" in w and "disagree" in w for w in warnings)


def _expected_tier_target(points, expected):
    """Human-readable smallest score movement that enters the expected tier band."""
    try:
        p = float(points)
    except (TypeError, ValueError):
        return {"delta": None, "text": "score delta unavailable"}
    if expected == "Pathogenic":
        delta = max(0.0, 10.0 - p)
        return {"delta": delta, "text": "increase net score to at least +10"}
    if expected == "Likely Pathogenic":
        if p < 6.0:
            return {"delta": 6.0 - p, "text": "increase net score to at least +6"}
        if p >= 10.0:
            return {"delta": 10.0 - p, "text": "decrease net score below +10"}
        return {"delta": 0.0, "text": "score already falls in the LP point band"}
    if expected == "VUS":
        if p < 0.0:
            return {"delta": -p, "text": "increase net score to at least 0"}
        if p >= 6.0:
            return {"delta": 6.0 - p, "text": "decrease net score below +6"}
        return {"delta": 0.0, "text": "score already falls in the VUS point band"}
    if expected == "Likely Benign":
        if p >= 0.0:
            return {"delta": -p, "text": "decrease net score below 0"}
        if p < -6.0:
            return {"delta": -6.0 - p, "text": "increase net score to at least -6"}
        return {"delta": 0.0, "text": "score already falls in the LB point band"}
    if expected == "Benign":
        delta = min(0.0, -6.0 - p)
        return {"delta": delta, "text": "decrease net score below -6 or justify BA1"}
    return {"delta": None, "text": "unknown expected tier"}


def _is_threshold_edge(points, expected):
    target = _expected_tier_target(points, expected)
    delta = target.get("delta")
    return isinstance(delta, (int, float)) and 0.0 < abs(delta) <= 1.0


def _configured_override_hint(result_case, fixture_case):
    """Best-effort signal that a scoped config override may be missing from scoring."""
    try:
        matches = C.BASE_CONFIG.matching_overrides(
            gene=(result_case or {}).get("gene") or (fixture_case or {}).get("gene"),
            vcep=(result_case or {}).get("ancestry") or (fixture_case or {}).get("vcep_group"),
            variant_key=(fixture_case or {}).get("variant_key"),
        )
    except Exception:
        matches = []
    return [m.get("id", "unnamed") for m in matches]


def classify_failure_cause(result_case, fixture_case, classification_detail=None):
    """Classify a serious discordance into one stable root-cause category.

    Categories are intentionally review-facing, not mutually exclusive biology:
    the first applicable primary cause is returned so reports can be counted.
    """
    fixture_case = fixture_case or {}
    criteria = _criteria(fixture_case)
    expected = (result_case or {}).get("expected")
    points = (result_case or {}).get("points")
    cls = classification_detail or _classification_detail(_signals(fixture_case))
    override_ids = _configured_override_hint(result_case, fixture_case)

    if _label_disagreement_hint(fixture_case):
        return "reference-label disagreement"
    if cls.get("overrides") and _has_opposing_evidence(expected, criteria):
        return "conflict-policy issue"
    if _has_standalone_benign(criteria) and tier_rank(expected) >= tier_rank("Likely Pathogenic"):
        return "conflict-policy issue"
    if override_ids and _has_opposing_evidence(expected, criteria):
        return "override absence"
    if not _has_direction_evidence(expected, criteria):
        return "evidence absence"
    if _is_threshold_edge(points, expected):
        return "threshold edge"
    return "strength mismatch"


def candidate_change_for_cause(cause, result_case, fixture_case):
    """Smallest reviewable change that could resolve a serious discordance."""
    fixture_case = fixture_case or {}
    expected = (result_case or {}).get("expected")
    points = (result_case or {}).get("points")
    target = _expected_tier_target(points, expected)
    delta = target.get("delta")
    delta_text = ""
    if isinstance(delta, (int, float)) and abs(delta) > 0:
        delta_text = " (smallest model-level point move: {:+.1f})".format(delta)

    if cause == "evidence absence":
        return {
            "candidate_change": (
                "Add or restore validated direction-appropriate ACMG evidence; "
                f"{target['text']}{delta_text}."
            ),
            "candidate_type": "data",
        }
    if cause == "strength mismatch":
        return {
            "candidate_change": (
                "Review supplied criterion strengths and opposing criteria against the source record; "
                f"{target['text']}{delta_text} if the reference label is retained."
            ),
            "candidate_type": "human review",
        }
    if cause == "threshold edge":
        return {
            "candidate_change": (
                "Prepare a threshold/config proposal only if credentialed review decides this "
                f"near-boundary case should move; {target['text']}{delta_text}."
            ),
            "candidate_type": "config proposal",
        }
    if cause == "override absence":
        return {
            "candidate_change": (
                "Add or activate a scoped VCEP/gene/variant override only after credentialed review "
                "records the exact scope and source."
            ),
            "candidate_type": "config proposal",
        }
    if cause == "conflict-policy issue":
        return {
            "candidate_change": (
                "Adjudicate the pathogenic-vs-benign evidence conflict; if accepted, record a "
                "variant-specific data correction or config proposal rather than changing scoring "
                "globally."
            ),
            "candidate_type": "human review",
        }
    if cause == "reference-label disagreement":
        return {
            "candidate_change": (
                "Resolve the reference-label disagreement with a credentialed adjudication packet; "
                "do not treat either source as silently authoritative."
            ),
            "candidate_type": "human review",
        }
    return {
        "candidate_change": "Review the source evidence packet and decide whether the issue is data, code, or scope.",
        "candidate_type": "human review",
    }


def _reviewer_disposition(result_case, fixture_case):
    """Return a recorded reviewer disposition from report or fixture metadata."""
    for source in (result_case or {}, fixture_case or {}):
        for key in ("adjudication", "review", "reviewer_decision"):
            block = source.get(key) or {}
            if not isinstance(block, dict):
                continue
            disposition = (
                block.get("reviewer_disposition")
                or block.get("disposition")
                or block.get("decision")
            )
            if disposition not in (None, ""):
                return disposition
    return None


def adjudication_record(failure_cause, candidate, result_case, fixture_case):
    """Machine-readable serious-discordance adjudication state."""
    disposition = _reviewer_disposition(result_case, fixture_case)
    unresolved = disposition in (None, "", "pending", "unresolved")
    return {
        "root_cause_category": failure_cause,
        "proposed_remediation": candidate["candidate_change"],
        "reviewer_disposition": disposition,
        "release_blocking": bool(unresolved),
        "release_blocking_reason": (
            "Unresolved pathogenic-vs-benign discordance requires reviewer disposition."
            if unresolved
            else None
        ),
    }


def _packet_classification(case, cls_detail):
    return {
        "tier": cls_detail.get("tier"),
        "total_points": cls_detail.get("total_points"),
        "engine_version": C.ENGINE_VERSION,
        "reconstruction_hash": cls_detail.get("reconstruction_hash"),
        "overrides": list(cls_detail.get("overrides") or []),
        "variant_key": (case or {}).get("variant_key"),
    }


def analyze(report, fixture, benchmark=None):
    """Build the full structured analysis from a report dict and fixture dict.

    Both arguments are the parsed JSON objects (each with a ``cases`` list).
    Returns a JSON-serializable analysis dict.
    """
    report_cases = report.get("cases", [])
    fixture_cases = fixture.get("cases", []) if isinstance(fixture, dict) else fixture
    fixture_by_id = {c["id"]: c for c in fixture_cases}

    mismatches = [c for c in report_cases if not c.get("match")]
    serious_cases = [c for c in report_cases if c.get("serious")]

    # Rollup accumulators ---------------------------------------------------- #
    by_expected = Counter()
    by_predicted = Counter()
    by_pair = Counter()
    by_pair_serious = Counter()
    by_gene = Counter()
    by_gene_serious = Counter()
    by_group = Counter()
    by_group_serious = Counter()
    by_crit_bucket = Counter()
    by_evidence = Counter()
    by_evidence_serious = Counter()
    signal_counts = Counter()  # 'REVEL', 'gnomAD AF', 'neither'
    serious_split = Counter()  # 'serious' / 'non_serious'
    by_failure_cause = Counter()
    release_blocking_serious = 0

    # gap key -> aggregate
    gaps = {}
    missing_in_fixture = 0

    # Per-mismatch evidence presence, fed to recommend_evidence_sources().
    mismatch_evidence = []

    for c in mismatches:
        cid = c["id"]
        fc = fixture_by_id.get(cid)
        if fc is None:
            missing_in_fixture += 1
        criteria = _criteria(fc)
        signals = _signals(fc)
        expected = c.get("expected")
        predicted = c.get("predicted")
        gene = c.get("gene")
        group = c.get("ancestry")
        is_serious = bool(c.get("serious"))

        by_expected[expected] += 1
        by_predicted[predicted] += 1
        by_pair[(expected, predicted)] += 1
        by_gene[gene] += 1
        by_group[group] += 1
        by_crit_bucket[criteria_count_bucket(len(criteria))] += 1
        serious_split["serious" if is_serious else "non_serious"] += 1

        present = signals_present(signals)
        if present:
            for s in present:
                signal_counts[s] += 1
        else:
            signal_counts["neither"] += 1

        _direction, category = classify_gap(expected, predicted, criteria, signals)
        by_evidence[category] += 1

        mismatch_evidence.append({
            "category": category,
            "serious": is_serious,
            "has_revel": signals.get("revel") is not None,
            "has_gnomad_af": signals.get("gnomad_af") is not None,
        })

        if is_serious:
            by_pair_serious[(expected, predicted)] += 1
            by_gene_serious[gene] += 1
            by_group_serious[group] += 1
            by_evidence_serious[category] += 1

        key = (expected, predicted, category)
        g = gaps.get(key)
        if g is None:
            g = gaps[key] = {
                "expected": expected,
                "predicted": predicted,
                "category": category,
                "count": 0,
                "serious": 0,
                "with_revel": 0,
                "with_gnomad_af": 0,
                "no_signals": 0,
                "example_ids": [],
            }
        g["count"] += 1
        if is_serious:
            g["serious"] += 1
        if signals.get("revel") is not None:
            g["with_revel"] += 1
        if signals.get("gnomad_af") is not None:
            g["with_gnomad_af"] += 1
        if not present:
            g["no_signals"] += 1
        if len(g["example_ids"]) < 5:
            g["example_ids"].append(cid)

    # Rank gaps: count desc, then serious desc, then stable on label ---------- #
    ranked_gaps = []
    for g in gaps.values():
        text = GAP_TEXT.get(g["category"], g["category"])
        g = dict(g)
        g["description"] = "%d '%s' cases predicted '%s': %s" % (
            g["count"],
            g["expected"],
            g["predicted"],
            text,
        )
        ranked_gaps.append(g)
    ranked_gaps.sort(
        key=lambda x: (-x["count"], -x["serious"], x["expected"], x["predicted"])
    )

    # Serious-error detail blocks ------------------------------------------- #
    serious_details = []
    review_packets = []
    for c in serious_cases:
        fc = fixture_by_id.get(c["id"])
        provenance = (fc or {}).get("provenance")
        criteria = _criteria(fc)
        signals = _signals(fc)
        cls_detail = _classification_detail(signals)
        failure_cause = classify_failure_cause(c, fc, cls_detail)
        candidate = candidate_change_for_cause(failure_cause, c, fc)
        adjudication = adjudication_record(failure_cause, candidate, c, fc)
        if adjudication["release_blocking"]:
            release_blocking_serious += 1
        by_failure_cause[failure_cause] += 1
        detail = {
            "id": c["id"],
            "gene": c.get("gene"),
            "group": c.get("ancestry"),
            "expected": c.get("expected"),
            "predicted": c.get("predicted"),
            "points": c.get("points"),
            "criteria": _criteria_summary(criteria),
            "criteria_rows": criteria_rows(criteria),
            "point_contributions": cls_detail["contributions"],
            "classification_overrides": cls_detail["overrides"],
            "classification_error": cls_detail["error"],
            "reconstructed_tier": cls_detail["tier"],
            "reconstructed_total_points": cls_detail["total_points"],
            "reconstruction_hash": cls_detail["reconstruction_hash"],
            "failure_cause": failure_cause,
            "candidate_change": candidate["candidate_change"],
            "candidate_type": candidate["candidate_type"],
            "root_cause_category": adjudication["root_cause_category"],
            "proposed_remediation": adjudication["proposed_remediation"],
            "reviewer_disposition": adjudication["reviewer_disposition"],
            "release_blocking": adjudication["release_blocking"],
            "adjudication": adjudication,
            "signals": {
                k: v
                for k, v in _signals(fc).items()
                if k in ("revel", "gnomad_af")
            },
            "provenance": provenance,
            "provenance_link": _provenance_link(provenance),
        }
        serious_details.append(detail)
        review_packets.append(
            build_validation_review_packet(
                benchmark=benchmark or report.get("benchmark"),
                case=fc or {"id": c.get("id"), "gene": c.get("gene"), "expected": c.get("expected")},
                result=c,
                classification=_packet_classification(fc, cls_detail),
                root_cause_category=adjudication["root_cause_category"],
                proposed_remediation=adjudication["proposed_remediation"],
                review_decision={
                    "status": "recorded" if adjudication["reviewer_disposition"] else "pending",
                    "reviewer_disposition": adjudication["reviewer_disposition"],
                },
                override_proposal={
                    "proposed": candidate["candidate_type"] == "config proposal",
                    "status": "pending" if candidate["candidate_type"] == "config proposal" else "not_proposed",
                },
            )
        )

    def _counter_to_rows(counter, serious_counter, key_name):
        rows = []
        for key, count in counter.most_common():
            rows.append(
                {key_name: key, "count": count, "serious": serious_counter.get(key, 0)}
            )
        return rows

    analysis = {
        "benchmark": benchmark or report.get("benchmark"),
        "engine_version": report.get("engine_version"),
        "run_utc": report.get("run_utc"),
        "gate_pass": report.get("gate_pass"),
        "metrics": report.get("metrics", {}),
        "totals": {
            "report_cases": len(report_cases),
            "mismatches": len(mismatches),
            "serious": len(serious_cases),
            "matched_in_fixture": len(mismatches) - missing_in_fixture,
            "missing_in_fixture": missing_in_fixture,
        },
        "rollups": {
            "by_expected": _counter_to_rows(by_expected, Counter(), "expected"),
            "by_predicted": _counter_to_rows(by_predicted, Counter(), "predicted"),
            "by_pair": [
                {
                    "expected": e,
                    "predicted": p,
                    "count": n,
                    "serious": by_pair_serious.get((e, p), 0),
                }
                for (e, p), n in by_pair.most_common()
            ],
            "by_gene": _counter_to_rows(by_gene, by_gene_serious, "gene"),
            "by_group": _counter_to_rows(by_group, by_group_serious, "group"),
            "by_criteria_count": _counter_to_rows(
                by_crit_bucket, Counter(), "bucket"
            ),
            "signals_present": dict(signal_counts),
            "by_evidence_type": [
                {
                    "category": cat,
                    "text": GAP_TEXT.get(cat, cat),
                    "count": n,
                    "serious": by_evidence_serious.get(cat, 0),
                }
                for cat, n in by_evidence.most_common()
            ],
            "serious_vs_nonserious": dict(serious_split),
            "serious_by_failure_cause": [
                {"failure_cause": cause, "count": count}
                for cause, count in by_failure_cause.most_common()
            ],
            "release_blocking_serious": release_blocking_serious,
        },
        "gaps": ranked_gaps,
        "evidence_recommendations": recommend_evidence_sources(mismatch_evidence),
        "serious_errors": serious_details,
        "review_packets": review_packets,
        "clinical_release_state": CLINICAL_RELEASE_STATE,
        "clinical_release_statement": CLINICAL_RELEASE_STATEMENT,
    }
    return analysis


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #


def _pct(x):
    try:
        return "%.1f%%" % (100.0 * float(x))
    except (TypeError, ValueError):
        return "--"


def render_markdown(analysis):
    """Render a compact triage Markdown report from an analysis dict."""
    a = analysis
    m = a.get("metrics", {})
    t = a["totals"]
    out = []
    w = out.append

    w("# Failure analysis -- `%s`" % a.get("benchmark"))
    w("")
    w("Engine `%s`  |  Run (UTC): %s  |  Gate: **%s**"
      % (a.get("engine_version"), a.get("run_utc"),
         "PASS" if a.get("gate_pass") else "FAIL"))
    w("")
    w("**%s**" % a.get("clinical_release_statement", CLINICAL_RELEASE_STATEMENT))
    w("")

    # Headline ------------------------------------------------------------- #
    w("## Headline")
    w("")
    w("| Metric | Value |")
    w("|---|---|")
    w("| Cases scored | %s |" % m.get("n", t["report_cases"]))
    w("| Mismatches (`match==false`) | %d |" % t["mismatches"])
    w("| Serious errors (`serious==true`) | %d |" % t["serious"])
    w("| Release-blocking serious errors | %d |" % a["rollups"].get("release_blocking_serious", 0))
    if "definitive_concordance" in m:
        w("| Concordance on definitive calls | %s |"
          % _pct(m.get("definitive_concordance")))
    if "overall_concordance" in m:
        w("| Overall exact-tier concordance | %s |"
          % _pct(m.get("overall_concordance")))
    w("")

    # Ranked gaps ---------------------------------------------------------- #
    w("## Top evidence/rule gaps to fix next (ranked by count)")
    w("")
    if a["gaps"]:
        w("| # | Count | Serious | Expected -> Predicted | Gap |")
        w("|---|---|---|---|---|")
        for i, g in enumerate(a["gaps"][:15], 1):
            w("| %d | %d | %d | %s -> %s | %s |"
              % (i, g["count"], g["serious"], g["expected"], g["predicted"],
                 GAP_TEXT.get(g["category"], g["category"])))
        w("")
        w("Plain-language summary of the top gaps:")
        w("")
        for g in a["gaps"][:5]:
            w("- %s _(e.g. %s)_"
              % (g["description"], ", ".join(g["example_ids"][:3]) or "n/a"))
    else:
        w("_No mismatches._")
    w("")

    # Ranked next evidence source ------------------------------------------ #
    recs = a.get("evidence_recommendations", [])
    w("## Recommended next evidence source (ranked by expected impact)")
    w("")
    actionable = [r for r in recs if r["count"]]
    if actionable:
        w("Each row counts mismatches that the source could plausibly help "
          "(missing criteria, or a currently absent REVEL/gnomAD signal).")
        w("")
        w("| # | Could help (cases) | of which serious | Evidence source to implement |")
        w("|---|---|---|---|")
        for i, r in enumerate(actionable, 1):
            w("| %d | %d | %d | %s |"
              % (i, r["count"], r["serious"], r["label"]))
    else:
        w("_No mismatches to attribute to a missing evidence source._")
    w("")

    # Mismatch rollups ----------------------------------------------------- #
    w("## Mismatch rollups")
    w("")

    w("### By expected -> predicted tier")
    w("")
    w("| Expected | Predicted | Count | Serious |")
    w("|---|---|---|---|")
    for row in a["rollups"]["by_pair"]:
        w("| %s | %s | %d | %d |"
          % (row["expected"], row["predicted"], row["count"], row["serious"]))
    w("")

    w("### By inferred missing-evidence type")
    w("")
    w("| Category | Count | Serious | Meaning |")
    w("|---|---|---|---|")
    for row in a["rollups"]["by_evidence_type"]:
        w("| `%s` | %d | %d | %s |"
          % (row["category"], row["count"], row["serious"], row["text"]))
    w("")

    w("### By gene (top 15)")
    w("")
    w("| Gene | Count | Serious |")
    w("|---|---|---|")
    for row in a["rollups"]["by_gene"][:15]:
        w("| %s | %d | %d |" % (row["gene"], row["count"], row["serious"]))
    w("")

    w("### By group / VCEP (top 15)")
    w("")
    w("| Group | Count | Serious |")
    w("|---|---|---|")
    for row in a["rollups"]["by_group"][:15]:
        w("| %s | %d | %d |" % (row["group"], row["count"], row["serious"]))
    w("")

    sig = a["rollups"]["signals_present"]
    cb = {r["bucket"]: r["count"] for r in a["rollups"]["by_criteria_count"]}
    ss = a["rollups"]["serious_vs_nonserious"]
    w("### Supplied-evidence shape")
    w("")
    w("- Criteria count: " + ", ".join(
        "%s=%d" % (b, cb.get(b, 0)) for b in ("0", "1-2", "3-4", "5+")))
    w("- Signals present across mismatches: REVEL=%d, gnomAD AF=%d, neither=%d"
      % (sig.get("REVEL", 0), sig.get("gnomAD AF", 0), sig.get("neither", 0)))
    w("- Serious vs non-serious mismatches: serious=%d, non-serious=%d"
      % (ss.get("serious", 0), ss.get("non_serious", 0)))
    w("")

    w("### Serious-discordance root causes")
    w("")
    causes = a["rollups"].get("serious_by_failure_cause", [])
    if causes:
        w("| Failure cause | Serious cases |")
        w("|---|---:|")
        for row in causes:
            w("| %s | %d |" % (row["failure_cause"], row["count"]))
    else:
        w("_No serious discordances._")
    w("")

    w("## Engineering fixes vs clinical sign-off")
    w("")
    w("- Engineering/data work may prepare evidence packets, matching fixes, and config proposals.")
    w("- Clinical sign-off decisions remain separate and must be made by credentialed reviewers.")
    w("- This report proposes candidate changes; it does not approve thresholds, overrides, or labels.")
    w("")

    # Serious detail blocks ------------------------------------------------- #
    w("## Serious errors (%d) -- detail" % len(a["serious_errors"]))
    w("")
    if not a["serious_errors"]:
        w("_None._")
    for d in a["serious_errors"]:
        w("### %s -- %s (%s)" % (d["id"], d["gene"], d.get("group") or "--"))
        w("")
        w("| Field | Value |")
        w("|---|---|")
        w("| Case ID | %s |" % d["id"])
        w("| Gene | %s |" % d["gene"])
        w("| Expected tier | %s |" % d["expected"])
        w("| Predicted tier | %s |" % d["predicted"])
        w("| Total points | %s |" % d["points"])
        w("| Reconstructed tier | %s |" % d.get("reconstructed_tier"))
        w("| Reconstruction hash | `%s` |" % (d.get("reconstruction_hash") or "--"))
        w("")

        w("#### Supplied criteria and source versions")
        w("")
        if d.get("criteria_rows"):
            w("| Criterion | Direction | Strength | Source | Source version |")
            w("|---|---|---|---|---|")
            for row in d["criteria_rows"]:
                w("| %s | %s | %s | %s | %s |" % (
                    row.get("criterion"),
                    row.get("direction") or "--",
                    row.get("strength") or "--",
                    row.get("source") or "--",
                    row.get("source_version") or "--",
                ))
        else:
            w("_No supplied ACMG criteria._")
        w("")

        w("#### Point-contribution table")
        w("")
        if d.get("classification_error"):
            w("_Could not reconstruct contribution table: %s._" % d["classification_error"])
        elif d.get("point_contributions"):
            w("| Criterion | Direction | Strength | Source | Source version | Points |")
            w("|---|---|---|---|---|---:|")
            for row in d["point_contributions"]:
                w("| %s | %s | %s | %s | %s | %s |" % (
                    row.get("criterion"),
                    row.get("direction") or "--",
                    row.get("strength") or "--",
                    row.get("source") or "--",
                    row.get("source_version") or "--",
                    row.get("points"),
                ))
        else:
            w("_No point-contributing evidence._")
        if d.get("classification_overrides"):
            w("")
            w("Classification overrides:")
            for ov in d["classification_overrides"]:
                w("- %s" % ov)
        w("")

        w("#### Root-cause classification and candidate resolution")
        w("")
        w("| Failure cause | Candidate change | Candidate type | Reviewer disposition | Release blocking |")
        w("|---|---|---|---|---|")
        w("| %s | %s | %s | %s | %s |" % (
            d.get("failure_cause"),
            d.get("candidate_change"),
            d.get("candidate_type"),
            d.get("reviewer_disposition") or "--",
            "yes" if d.get("release_blocking") else "no",
        ))
        w("")
        if d["signals"]:
            w("- Signals: %s"
              % ", ".join("%s=%s" % (k, v) for k, v in d["signals"].items()))
        if d.get("provenance"):
            prov = d["provenance"]
            bits = ", ".join("%s=%s" % (k, v) for k, v in prov.items())
            w("- Provenance: %s" % bits)
        if d.get("provenance_link"):
            w("- Link: %s" % d["provenance_link"])
        w("")

    return "\n".join(out).rstrip() + "\n"


def render_stdout_summary(analysis):
    """Short human summary printed to stdout."""
    a = analysis
    t = a["totals"]
    lines = []
    lines.append("Benchmark: %s" % a.get("benchmark"))
    lines.append("Cases: %s   Mismatches: %d   Serious errors: %d"
                 % (a["metrics"].get("n", t["report_cases"]),
                    t["mismatches"], t["serious"]))
    lines.append("")
    lines.append("Top gaps to fix next:")
    if a["gaps"]:
        for i, g in enumerate(a["gaps"][:5], 1):
            lines.append("  %d. %s" % (i, g["description"]))
    else:
        lines.append("  (none)")
    recs = [r for r in a.get("evidence_recommendations", []) if r["count"]]
    if recs:
        lines.append("")
        lines.append("Next evidence source by expected impact:")
        for i, r in enumerate(recs, 1):
            lines.append("  %d. %s -- could help %d cases (%d serious)"
                         % (i, r["label"], r["count"], r["serious"]))
    if a["serious_errors"]:
        lines.append("")
        lines.append("Serious errors:")
        for d in a["serious_errors"]:
            lines.append("  - %s %s: %s -> %s (pts=%s; cause=%s)"
                         % (d["id"], d["gene"], d["expected"],
                            d["predicted"], d["points"], d.get("failure_cause")))
        causes = a["rollups"].get("serious_by_failure_cause", [])
        if causes:
            lines.append("")
            lines.append("Serious root-cause breakdown:")
            for row in causes:
                lines.append("  - %s: %d" % (row["failure_cause"], row["count"]))
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Path resolution / IO
# --------------------------------------------------------------------------- #


def _model_dir():
    """The ``ReClass Model/`` directory, derived from this file's location."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def resolve_report_path(name, reports_dir):
    """Map a benchmark name to its report JSON path.

    ``synthetic_v1`` is the unsuffixed ``validation_report.json`` special case;
    every other benchmark is ``validation_report_<name>.json``.
    """
    if name == "synthetic_v1":
        fname = "validation_report.json"
    else:
        fname = "validation_report_%s.json" % name
    return os.path.join(reports_dir, fname)


def resolve_fixture_path(name, fixtures_dir):
    return os.path.join(fixtures_dir, "%s.json" % name)


def _load_json(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def run(name, model_dir=None):
    """Resolve paths, load files, analyze, write outputs, return analysis."""
    model_dir = model_dir or _model_dir()
    reports_dir = os.path.join(model_dir, "validation", "reports")
    fixtures_dir = os.path.join(model_dir, "validation", "fixtures")

    report_path = resolve_report_path(name, reports_dir)
    fixture_path = resolve_fixture_path(name, fixtures_dir)

    if not os.path.exists(report_path):
        raise SystemExit("Report not found for benchmark '%s': %s"
                         % (name, report_path))
    if not os.path.exists(fixture_path):
        raise SystemExit("Fixture not found for benchmark '%s': %s"
                         % (name, fixture_path))

    report = _load_json(report_path)
    fixture = _load_json(fixture_path)

    analysis = analyze(report, fixture, benchmark=name)
    analysis["report_path"] = report_path
    analysis["fixture_path"] = fixture_path

    md_path = os.path.join(reports_dir, "failure_analysis_%s.md" % name)
    json_path = os.path.join(reports_dir, "failure_analysis_%s.json" % name)
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write(render_markdown(analysis))
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(analysis, fh, indent=2)

    analysis["_md_path"] = md_path
    analysis["_json_path"] = json_path
    return analysis


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Triage why the engine got validation cases wrong.")
    parser.add_argument(
        "benchmark",
        help="benchmark name, e.g. clinvar_real_v1 / clingen_real_v1 / synthetic_v1")
    args = parser.parse_args(argv)

    analysis = run(args.benchmark)
    print(render_stdout_summary(analysis))
    print("")
    print("Wrote: %s" % os.path.relpath(analysis["_md_path"], _model_dir()))
    print("Wrote: %s" % os.path.relpath(analysis["_json_path"], _model_dir()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
