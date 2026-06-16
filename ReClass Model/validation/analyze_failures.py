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
from collections import Counter, defaultdict

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
    for c in serious_cases:
        fc = fixture_by_id.get(c["id"])
        provenance = (fc or {}).get("provenance")
        criteria = _criteria(fc)
        serious_details.append(
            {
                "id": c["id"],
                "gene": c.get("gene"),
                "group": c.get("ancestry"),
                "expected": c.get("expected"),
                "predicted": c.get("predicted"),
                "points": c.get("points"),
                "criteria": _criteria_summary(criteria),
                "signals": {
                    k: v
                    for k, v in _signals(fc).items()
                    if k in ("revel", "gnomad_af")
                },
                "provenance": provenance,
                "provenance_link": _provenance_link(provenance),
            }
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
        },
        "gaps": ranked_gaps,
        "evidence_recommendations": recommend_evidence_sources(mismatch_evidence),
        "serious_errors": serious_details,
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

    # Headline ------------------------------------------------------------- #
    w("## Headline")
    w("")
    w("| Metric | Value |")
    w("|---|---|")
    w("| Cases scored | %s |" % m.get("n", t["report_cases"]))
    w("| Mismatches (`match==false`) | %d |" % t["mismatches"])
    w("| Serious errors (`serious==true`) | %d |" % t["serious"])
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

    # Serious detail blocks ------------------------------------------------- #
    w("## Serious errors (%d) -- detail" % len(a["serious_errors"]))
    w("")
    if not a["serious_errors"]:
        w("_None._")
    for d in a["serious_errors"]:
        w("### %s -- %s (%s)" % (d["id"], d["gene"], d.get("group") or "--"))
        w("")
        w("- Expected **%s** -> predicted **%s**  (points: %s)"
          % (d["expected"], d["predicted"], d["points"]))
        w("- Supplied criteria: %s"
          % (", ".join(d["criteria"]) if d["criteria"] else "_none_"))
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
            lines.append("  - %s %s: %s -> %s (pts=%s)"
                         % (d["id"], d["gene"], d["expected"],
                            d["predicted"], d["points"]))
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
