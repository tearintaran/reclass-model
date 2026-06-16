#!/usr/bin/env python3
"""Before/after comparison tool for two validation benchmark reports.

Compares any two validation reports written by ``validation/harness.py`` and
summarizes what changed: case counts, definitive/overall concordance, serious
discordance, per-tier recall, confusion-matrix deltas, matched-case overlap, and
(optionally, when fixtures exist) evidence coverage.

This tool compares any two available validation reports and
becomes especially useful afterwards by comparing ``clinvar_real_v1`` with the
enriched ``clinvar_enriched_v1`` fixture.

Stdlib-only CLI. Run from the ``ReClass Model/`` directory::

    PY="../.venv/bin/python"
    $PY validation/compare_reports.py synthetic_v1 clingen_real_v1
    $PY validation/compare_reports.py clinvar_real_v1 clinvar_enriched_v1

It resolves each benchmark name to its report JSON (and matching fixture, if
present), computes the comparison, and writes::

    validation/reports/comparison_<before>_vs_<after>.json
    validation/reports/comparison_<before>_vs_<after>.md

The comparison core (:func:`compare`, :func:`per_tier_recall`,
:func:`confusion_matrix`, :func:`overlap_changes`, ...) takes plain dicts and has
no filesystem dependency, so it is unit-testable with tiny in-memory reports and
does not require live evidence-provider calls.

It never modifies the baseline validation reports it reads.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys

# --------------------------------------------------------------------------- #
# Tier model (mirrors the harness ordering)
# --------------------------------------------------------------------------- #

TIERS = ["Benign", "Likely Benign", "VUS", "Likely Pathogenic", "Pathogenic"]

TIER_RANK = {
    "Pathogenic": 4,
    "Likely Pathogenic": 3,
    "VUS": 2,
    "Likely Benign": 1,
    "Benign": 0,
}

# Max number of example case ids retained per overlap bucket.
_MAX_EXAMPLES = 10


def tier_rank(tier):
    """Numeric rank for an ACMG tier; unknown tiers sort as VUS-neutral (2)."""
    return TIER_RANK.get(tier, 2)


# --------------------------------------------------------------------------- #
# Path resolution
# --------------------------------------------------------------------------- #


def _model_dir():
    """The ``ReClass Model/`` directory, derived from this file's location."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def resolve_report_path(name, reports_dir):
    """Map a benchmark name to its validation report JSON path.

    ``synthetic_v1`` is the unsuffixed ``validation_report.json`` special case;
    every other benchmark is ``validation_report_<name>.json``.
    """
    if name == "synthetic_v1":
        fname = "validation_report.json"
    else:
        fname = "validation_report_%s.json" % name
    return os.path.join(reports_dir, fname)


def resolve_fixture_path(name, fixtures_dir):
    """Map a benchmark name to its fixture JSON path."""
    return os.path.join(fixtures_dir, "%s.json" % name)


def harness_hint(name):
    """The command a user should run to produce a missing report."""
    return "validation/harness.py %s" % ("" if name == "synthetic_v1" else name)


# --------------------------------------------------------------------------- #
# Metric helpers (pure: dicts in, dict out)
# --------------------------------------------------------------------------- #


def _num_delta(before, after):
    """A {before, after, delta} block; delta is None if either side is missing."""
    block = {"before": before, "after": after}
    if isinstance(before, (int, float)) and isinstance(after, (int, float)):
        block["delta"] = after - before
    else:
        block["delta"] = None
    return block


def metric_deltas(before_metrics, after_metrics):
    """Headline metric deltas from two ``metrics`` blocks.

    Covers case count, definitive concordance, serious count/rate, and overall
    exact concordance. Missing values produce ``delta = None`` rather than raising.
    """
    bm = before_metrics or {}
    am = after_metrics or {}
    return {
        "case_count": _num_delta(bm.get("n"), am.get("n")),
        "definitive_n": _num_delta(bm.get("definitive_n"), am.get("definitive_n")),
        "definitive_concordance": _num_delta(
            bm.get("definitive_concordance"), am.get("definitive_concordance")
        ),
        "serious_count": _num_delta(bm.get("serious_count"), am.get("serious_count")),
        "serious_rate": _num_delta(bm.get("serious_rate"), am.get("serious_rate")),
        "overall_concordance": _num_delta(
            bm.get("overall_concordance"), am.get("overall_concordance")
        ),
    }


def per_tier_recall(cases):
    """Recall per *expected* tier from a report's ``cases`` list.

    Recall here = fraction of cases whose expected tier was reproduced exactly
    (``match == true``). Returns ``{tier: {n, matched, recall}}`` for every tier
    in :data:`TIERS` (zero-filled when a tier is absent).
    """
    out = {t: {"n": 0, "matched": 0, "recall": 0.0} for t in TIERS}
    for c in cases or []:
        expected = c.get("expected")
        bucket = out.get(expected)
        if bucket is None:
            bucket = out[expected] = {"n": 0, "matched": 0, "recall": 0.0}
        bucket["n"] += 1
        if c.get("match"):
            bucket["matched"] += 1
    for bucket in out.values():
        bucket["recall"] = bucket["matched"] / bucket["n"] if bucket["n"] else 0.0
    return out


def per_tier_recall_delta(before_cases, after_cases):
    """Per-tier recall for both sides plus the recall delta."""
    before = per_tier_recall(before_cases)
    after = per_tier_recall(after_cases)
    tiers = list(TIERS)
    for t in list(before) + list(after):
        if t not in tiers:
            tiers.append(t)
    out = {}
    for t in tiers:
        b = before.get(t, {"n": 0, "matched": 0, "recall": 0.0})
        a = after.get(t, {"n": 0, "matched": 0, "recall": 0.0})
        out[t] = {
            "before": b,
            "after": a,
            "recall_delta": a["recall"] - b["recall"],
        }
    return out


def confusion_matrix(cases):
    """Expected->predicted confusion counts as ``{expected: {predicted: n}}``.

    Tiers outside :data:`TIERS` are tolerated and added on demand.
    """
    matrix = {e: {p: 0 for p in TIERS} for e in TIERS}
    for c in cases or []:
        e = c.get("expected")
        p = c.get("predicted")
        row = matrix.setdefault(e, {})
        row[p] = row.get(p, 0) + 1
    return matrix


def confusion_delta(before_cases, after_cases):
    """Cell-by-cell confusion-matrix delta (after - before).

    Returns ``{expected: {predicted: delta}}`` including only non-zero deltas so
    the rendered table stays readable.
    """
    before = confusion_matrix(before_cases)
    after = confusion_matrix(after_cases)
    expecteds = set(before) | set(after)
    delta = {}
    for e in expecteds:
        b_row = before.get(e, {})
        a_row = after.get(e, {})
        predicteds = set(b_row) | set(a_row)
        row_delta = {}
        for p in predicteds:
            d = a_row.get(p, 0) - b_row.get(p, 0)
            if d != 0:
                row_delta[p] = d
        if row_delta:
            delta[e] = row_delta
    return delta


def overlap_changes(before_cases, after_cases):
    """Matched-case overlap plus improved/worsened/unchanged among shared ids.

    "Improved"/"worsened" are judged by the absolute tier-rank distance between a
    case's expected and predicted tier: a case that lands closer to its expected
    tier in *after* improved, farther away worsened, equal distance is unchanged.
    This captures partial gains (e.g. Pathogenic->VUS becoming
    Pathogenic->Likely Pathogenic), not only exact match flips.
    """
    before_by_id = {c.get("id"): c for c in (before_cases or [])}
    after_by_id = {c.get("id"): c for c in (after_cases or [])}
    before_ids = set(before_by_id)
    after_ids = set(after_by_id)
    overlap_ids = before_ids & after_ids

    improved, worsened, unchanged = [], [], []
    became_match, lost_match = [], []
    for cid in sorted(overlap_ids, key=lambda x: (str(x))):
        b = before_by_id[cid]
        a = after_by_id[cid]
        expected = a.get("expected", b.get("expected"))
        b_dist = abs(tier_rank(expected) - tier_rank(b.get("predicted")))
        a_dist = abs(tier_rank(expected) - tier_rank(a.get("predicted")))
        if a_dist < b_dist:
            improved.append(cid)
        elif a_dist > b_dist:
            worsened.append(cid)
        else:
            unchanged.append(cid)
        if not b.get("match") and a.get("match"):
            became_match.append(cid)
        elif b.get("match") and not a.get("match"):
            lost_match.append(cid)

    def _ex(ids):
        return list(ids[:_MAX_EXAMPLES])

    return {
        "before_n": len(before_ids),
        "after_n": len(after_ids),
        "overlap_n": len(overlap_ids),
        "only_before_n": len(before_ids - after_ids),
        "only_after_n": len(after_ids - before_ids),
        "improved": len(improved),
        "worsened": len(worsened),
        "unchanged": len(unchanged),
        "became_match": len(became_match),
        "lost_match": len(lost_match),
        "improved_ids": _ex(improved),
        "worsened_ids": _ex(worsened),
        "became_match_ids": _ex(became_match),
        "lost_match_ids": _ex(lost_match),
    }


# --------------------------------------------------------------------------- #
# Optional fixture-based evidence coverage
# --------------------------------------------------------------------------- #


def _criteria_count(case):
    return len((case.get("signals", {}) or {}).get("criteria", []) or [])


def _criteria_bucket(n):
    if n == 0:
        return "0"
    if n <= 2:
        return "1-2"
    if n <= 4:
        return "3-4"
    return "5+"


def evidence_coverage(fixture):
    """Evidence-coverage counts for a single fixture dict.

    Returns ``None`` when no fixture is available. Otherwise counts cases with
    criteria, REVEL, AF/gnomAD frequency, and enrichment metadata, plus a
    criteria-count bucket histogram.
    """
    if not fixture:
        return None
    cases = fixture.get("cases", []) if isinstance(fixture, dict) else fixture
    cov = {
        "cases": 0,
        "with_criteria": 0,
        "with_revel": 0,
        "with_gnomad_af": 0,
        "with_enrichment": 0,
        "criteria_buckets": {"0": 0, "1-2": 0, "3-4": 0, "5+": 0},
    }
    for c in cases or []:
        cov["cases"] += 1
        signals = c.get("signals", {}) or {}
        n_crit = len(signals.get("criteria", []) or [])
        if n_crit:
            cov["with_criteria"] += 1
        if signals.get("revel") is not None:
            cov["with_revel"] += 1
        if signals.get("gnomad_af") is not None:
            cov["with_gnomad_af"] += 1
        if c.get("enrichment"):
            cov["with_enrichment"] += 1
        cov["criteria_buckets"][_criteria_bucket(n_crit)] += 1
    return cov


def evidence_coverage_delta(before_fixture, after_fixture):
    """Evidence coverage for both fixtures plus per-field deltas.

    Returns ``None`` if neither fixture is available.
    """
    before = evidence_coverage(before_fixture)
    after = evidence_coverage(after_fixture)
    if before is None and after is None:
        return None

    b = before or {}
    a = after or {}

    def _d(key):
        return _num_delta(b.get(key), a.get(key))

    bucket_delta = {}
    bb = b.get("criteria_buckets", {})
    ab = a.get("criteria_buckets", {})
    for bucket in ("0", "1-2", "3-4", "5+"):
        bucket_delta[bucket] = _num_delta(bb.get(bucket), ab.get(bucket))

    return {
        "before": before,
        "after": after,
        "delta": {
            "cases": _d("cases"),
            "with_criteria": _d("with_criteria"),
            "with_revel": _d("with_revel"),
            "with_gnomad_af": _d("with_gnomad_af"),
            "with_enrichment": _d("with_enrichment"),
            "criteria_buckets": bucket_delta,
        },
    }


# --------------------------------------------------------------------------- #
# Top-level comparison
# --------------------------------------------------------------------------- #


def compare(
    before_report,
    after_report,
    before_name=None,
    after_name=None,
    before_fixture=None,
    after_fixture=None,
):
    """Build the full comparison dict from two report dicts.

    ``before_report`` / ``after_report`` are parsed harness report objects (each
    with ``metrics`` and ``cases``). Fixtures are optional and only used for the
    evidence-coverage section. Returns a JSON-serializable dict.
    """
    before_cases = before_report.get("cases", [])
    after_cases = after_report.get("cases", [])

    comparison = {
        "before": before_name or before_report.get("benchmark"),
        "after": after_name or after_report.get("benchmark"),
        "engine_version_before": before_report.get("engine_version"),
        "engine_version_after": after_report.get("engine_version"),
        "run_utc_before": before_report.get("run_utc"),
        "run_utc_after": after_report.get("run_utc"),
        "gate_pass_before": before_report.get("gate_pass"),
        "gate_pass_after": after_report.get("gate_pass"),
        "metrics": metric_deltas(
            before_report.get("metrics"), after_report.get("metrics")
        ),
        "per_tier_recall": per_tier_recall_delta(before_cases, after_cases),
        "confusion_delta": confusion_delta(before_cases, after_cases),
        "overlap": overlap_changes(before_cases, after_cases),
    }

    coverage = evidence_coverage_delta(before_fixture, after_fixture)
    if coverage is not None:
        comparison["evidence_coverage"] = coverage

    return comparison


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #


def _pct(x):
    try:
        return "%.1f%%" % (100.0 * float(x))
    except (TypeError, ValueError):
        return "--"


def _signed_pct(x):
    if not isinstance(x, (int, float)):
        return "--"
    return "%+.1f pp" % (100.0 * x)


def _signed_int(x):
    if not isinstance(x, (int, float)):
        return "--"
    return "%+d" % int(round(x))


def _fmt_val(x, kind):
    if x is None:
        return "--"
    if kind == "pct":
        return _pct(x)
    if kind == "int":
        return str(int(x)) if float(x).is_integer() else str(x)
    return str(x)


def _delta_row(label, block, kind):
    """A markdown ``| metric | before | after | delta |`` row."""
    before = _fmt_val(block.get("before"), kind)
    after = _fmt_val(block.get("after"), kind)
    delta = block.get("delta")
    if kind == "pct":
        delta_s = _signed_pct(delta)
    else:
        delta_s = _signed_int(delta)
    return "| %s | %s | %s | %s |" % (label, before, after, delta_s)


def render_markdown(comparison):
    """Render the comparison dict as a compact human-readable Markdown report."""
    c = comparison
    out = []
    w = out.append

    w("# Validation comparison -- `%s` -> `%s`" % (c["before"], c["after"]))
    w("")
    gate_b = "PASS" if c.get("gate_pass_before") else "FAIL"
    gate_a = "PASS" if c.get("gate_pass_after") else "FAIL"
    w("Engine `%s` -> `%s`  |  Gate **%s** -> **%s**"
      % (c.get("engine_version_before"), c.get("engine_version_after"),
         gate_b, gate_a))
    w("")

    # Headline metric deltas ------------------------------------------------ #
    m = c["metrics"]
    w("## Headline metric deltas")
    w("")
    w("| Metric | Before | After | Delta |")
    w("|---|---|---|---|")
    w(_delta_row("Cases scored", m["case_count"], "int"))
    w(_delta_row("Definitive calls (n)", m["definitive_n"], "int"))
    w(_delta_row("Definitive concordance", m["definitive_concordance"], "pct"))
    w(_delta_row("Overall exact concordance", m["overall_concordance"], "pct"))
    w(_delta_row("Serious discordance count", m["serious_count"], "int"))
    w(_delta_row("Serious discordance rate", m["serious_rate"], "pct"))
    w("")

    # Per-tier recall ------------------------------------------------------- #
    w("## Per-tier recall (expected tier reproduced exactly)")
    w("")
    w("| Expected tier | Before n | Before recall | After n | After recall | Recall delta |")
    w("|---|---|---|---|---|---|")
    for tier in TIERS:
        row = c["per_tier_recall"].get(tier)
        if row is None:
            continue
        b = row["before"]
        a = row["after"]
        w("| %s | %d | %s | %d | %s | %s |"
          % (tier, b["n"], _pct(b["recall"]), a["n"], _pct(a["recall"]),
             _signed_pct(row["recall_delta"])))
    w("")

    # Overlap --------------------------------------------------------------- #
    o = c["overlap"]
    w("## Matched-case overlap")
    w("")
    w("| Metric | Value |")
    w("|---|---|")
    w("| Cases in before | %d |" % o["before_n"])
    w("| Cases in after | %d |" % o["after_n"])
    w("| Overlapping ids | %d |" % o["overlap_n"])
    w("| Only in before | %d |" % o["only_before_n"])
    w("| Only in after | %d |" % o["only_after_n"])
    w("| Improved (closer to expected) | %d |" % o["improved"])
    w("| Worsened (farther from expected) | %d |" % o["worsened"])
    w("| Unchanged distance | %d |" % o["unchanged"])
    w("| Became exact match | %d |" % o["became_match"])
    w("| Lost exact match | %d |" % o["lost_match"])
    w("")
    if o["improved_ids"]:
        w("- Example improved: %s" % ", ".join(str(x) for x in o["improved_ids"]))
    if o["worsened_ids"]:
        w("- Example worsened: %s" % ", ".join(str(x) for x in o["worsened_ids"]))
    if o["improved_ids"] or o["worsened_ids"]:
        w("")

    # Confusion delta ------------------------------------------------------- #
    w("## Confusion-matrix deltas (after - before)")
    w("")
    cd = c["confusion_delta"]
    if not cd:
        w("_No confusion-matrix changes._")
    else:
        w("| Expected | Predicted | Delta |")
        w("|---|---|---|")
        for e in TIERS + [e for e in cd if e not in TIERS]:
            row = cd.get(e)
            if not row:
                continue
            for p in TIERS + [p for p in row if p not in TIERS]:
                if p in row:
                    w("| %s | %s | %s |" % (e, p, _signed_int(row[p])))
    w("")

    # Evidence coverage ----------------------------------------------------- #
    cov = c.get("evidence_coverage")
    if cov is not None:
        w("## Evidence coverage (from fixtures)")
        w("")
        d = cov["delta"]
        w("| Coverage | Before | After | Delta |")
        w("|---|---|---|---|")
        w(_delta_row("Cases", d["cases"], "int"))
        w(_delta_row("With criteria", d["with_criteria"], "int"))
        w(_delta_row("With REVEL", d["with_revel"], "int"))
        w(_delta_row("With gnomAD AF", d["with_gnomad_af"], "int"))
        w(_delta_row("With enrichment metadata", d["with_enrichment"], "int"))
        w("")
        w("Criteria-count buckets:")
        w("")
        w("| Bucket | Before | After | Delta |")
        w("|---|---|---|---|")
        for bucket in ("0", "1-2", "3-4", "5+"):
            w(_delta_row(bucket, d["criteria_buckets"][bucket], "int"))
        w("")

    w("---")
    w("*Generated by validation/compare_reports.py comparing `%s` and `%s`.*"
      % (c["before"], c["after"]))
    return "\n".join(out).rstrip() + "\n"


def render_stdout_summary(comparison):
    """Short human summary printed to stdout."""
    c = comparison
    m = c["metrics"]
    o = c["overlap"]
    lines = []
    lines.append("Comparison: %s -> %s" % (c["before"], c["after"]))
    lines.append("Cases: %s -> %s (%s)"
                 % (m["case_count"]["before"], m["case_count"]["after"],
                    _signed_int(m["case_count"]["delta"])))
    lines.append("Definitive concordance: %s -> %s (%s)"
                 % (_pct(m["definitive_concordance"]["before"]),
                    _pct(m["definitive_concordance"]["after"]),
                    _signed_pct(m["definitive_concordance"]["delta"])))
    lines.append("Overall exact concordance: %s -> %s (%s)"
                 % (_pct(m["overall_concordance"]["before"]),
                    _pct(m["overall_concordance"]["after"]),
                    _signed_pct(m["overall_concordance"]["delta"])))
    lines.append("Serious discordance: %s -> %s (%s)"
                 % (m["serious_count"]["before"], m["serious_count"]["after"],
                    _signed_int(m["serious_count"]["delta"])))
    lines.append("Overlap: %d shared ids; improved=%d worsened=%d unchanged=%d"
                 % (o["overlap_n"], o["improved"], o["worsened"], o["unchanged"]))
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# IO / orchestration
# --------------------------------------------------------------------------- #


def _load_json(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _load_report_or_fail(name, reports_dir):
    path = resolve_report_path(name, reports_dir)
    if not os.path.exists(path):
        raise SystemExit(
            "Report not found for benchmark '%s': %s\n"
            "Generate it first with:\n    %s"
            % (name, path, harness_hint(name))
        )
    try:
        return _load_json(path)
    except json.JSONDecodeError as exc:
        raise SystemExit(
            "Report exists but is not valid JSON for benchmark '%s': %s\n"
            "This can happen if validation/harness.py was still writing the report. "
            "Regenerate it, then run compare_reports.py after the harness exits:\n"
            "    %s\n"
            "JSON error: %s"
            % (name, path, harness_hint(name), exc)
        )


def _maybe_load_fixture(name, fixtures_dir):
    path = resolve_fixture_path(name, fixtures_dir)
    if os.path.exists(path):
        try:
            return _load_json(path)
        except (ValueError, OSError):
            return None
    return None


def run(before, after, model_dir=None):
    """Resolve paths, load reports/fixtures, compare, write outputs.

    Returns the comparison dict with ``_md_path`` / ``_json_path`` attached.
    Raises ``SystemExit`` with a clear hint when a report is missing.
    """
    model_dir = model_dir or _model_dir()
    reports_dir = os.path.join(model_dir, "validation", "reports")
    fixtures_dir = os.path.join(model_dir, "validation", "fixtures")

    before_report = _load_report_or_fail(before, reports_dir)
    after_report = _load_report_or_fail(after, reports_dir)
    before_fixture = _maybe_load_fixture(before, fixtures_dir)
    after_fixture = _maybe_load_fixture(after, fixtures_dir)

    comparison = compare(
        before_report,
        after_report,
        before_name=before,
        after_name=after,
        before_fixture=before_fixture,
        after_fixture=after_fixture,
    )
    comparison["generated_utc"] = datetime.datetime.now(
        datetime.timezone.utc
    ).isoformat()

    os.makedirs(reports_dir, exist_ok=True)
    stem = "comparison_%s_vs_%s" % (before, after)
    md_path = os.path.join(reports_dir, stem + ".md")
    json_path = os.path.join(reports_dir, stem + ".json")
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write(render_markdown(comparison))
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(comparison, fh, indent=2)
        fh.write("\n")

    comparison["_md_path"] = md_path
    comparison["_json_path"] = json_path
    return comparison


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Compare two validation benchmark reports (before vs after).")
    parser.add_argument("before", help="baseline benchmark name, e.g. clinvar_real_v1")
    parser.add_argument("after", help="comparison benchmark name, e.g. clinvar_enriched_v1")
    args = parser.parse_args(argv)

    comparison = run(args.before, args.after)
    print(render_stdout_summary(comparison))
    print("")
    print("Wrote: %s" % os.path.relpath(comparison["_md_path"], _model_dir()))
    print("Wrote: %s" % os.path.relpath(comparison["_json_path"], _model_dir()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
