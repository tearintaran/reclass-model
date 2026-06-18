"""Concordance harness + release gate (spec 12 / memo S?).

Exit code 0 = GATE PASS, exit code 2 = GATE FAIL.

The gate, and what "ready to be validated" means:
  * definitive-call concordance >= 85%  (expected non-VUS calls reproduced), AND
  * serious (pathogenic <-> benign) discordance < 1%.

Metrics are reported PER ANCESTRY GROUP and PER ENGINE VERSION — never as one
pooled number — because a pooled figure can hide a systematically weaker cohort.
Writes reports/validation_report.{json,md}.
"""

from __future__ import annotations

import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.scoring import classify_signals  # noqa: E402
from engine import config as C  # noqa: E402

# Coverage/recall shapes are lifted from compare_reports so the harness and the
# before/after tool agree on bucketing. Guarded import keeps both invocation
# styles working: ``python validation/harness.py`` (script dir on path) and
# ``from validation import harness`` (model dir on path).
try:
    from validation import compare_reports as _cr  # noqa: E402
except Exception:  # pragma: no cover - script-dir fallback
    import compare_reports as _cr  # type: ignore  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURES_DIR = os.path.join(HERE, "fixtures")
REPORTS_DIR = os.path.join(HERE, "reports")

TIERS = ["Benign", "Likely Benign", "VUS", "Likely Pathogenic", "Pathogenic"]
_PATHO = {"Likely Pathogenic", "Pathogenic"}
_BENIGN = {"Benign", "Likely Benign"}

# Evidence classes used for class-level recall and provider breakdowns.
_CLASSES = {"pathogenic": _PATHO, "benign": _BENIGN, "vus": {"VUS"}}

# Recognised true-ancestry labels (gnomAD-style population groups + synthetic
# benchmark labels). Anything containing "VCEP"/"panel" is a clinical expert
# panel grouping, not an ancestry; everything else is treated as unspecified so
# a VCEP/panel name is never silently reported as an ancestry.
KNOWN_ANCESTRIES = {
    "european", "non-finnish european", "finnish", "african",
    "african american", "african/african american", "latino",
    "admixed american", "latino/admixed american", "east asian",
    "south asian", "asian", "ashkenazi", "ashkenazi jewish",
    "middle eastern", "amish", "other", "admixed", "multiple",
}

# Provider presence flags -> human label, used by the provider-coverage table.
_PROVIDER_FLAGS = {
    "clingen": "has_clingen",
    "revel": "has_revel",
    "gnomad_af": "has_gnomad_af",
}

DEFINITIVE_CONCORDANCE_BAR = 0.85
SERIOUS_DISCORDANCE_BAR = 0.01


def _is_serious(expected: str, predicted: str) -> bool:
    return (
        (expected in _PATHO and predicted in _BENIGN)
        or (expected in _BENIGN and predicted in _PATHO)
    )


def grouping_kind(label) -> str:
    """Classify a fixture ``ancestry`` value as ancestry / panel / unspecified.

    The ``ancestry`` field is overloaded across benchmarks: synthetic fixtures
    use real ancestries (``European``), ClinGen uses VCEP/panel names
    (``Phenylketonuria VCEP``), and ClinVar uses ``Unspecified``. This keeps the
    report from presenting a VCEP grouping as if it were an ancestry stratum.
    """
    if label is None:
        return "unspecified"
    s = str(label).strip().lower()
    if not s or s in {"unspecified", "unknown", "na", "n/a", "none", "not provided"}:
        return "unspecified"
    if "vcep" in s or "panel" in s or "working group" in s:
        return "panel"
    if s in KNOWN_ANCESTRIES:
        return "ancestry"
    return "unspecified"


def case_evidence(case: dict) -> dict:
    """Pure: derive evidence-presence flags from a single fixture case dict.

    Per-provider coverage is read from the fixture ``signals`` (and optional
    ``enrichment`` block) only -- never by importing an evidence provider -- so
    this stays decoupled from live evidence-provider availability.
    """
    signals = case.get("signals", {}) or {}
    criteria = signals.get("criteria", []) or []
    enrichment = case.get("enrichment")

    has_clingen = any((c or {}).get("source") == "clingen" for c in criteria)
    if enrichment and "clingen_erepo" in (enrichment.get("providers") or []):
        has_clingen = True

    flags = {
        "n_criteria": len(criteria),
        "has_revel": signals.get("revel") is not None,
        "has_gnomad_af": signals.get("gnomad_af") is not None,
        "has_clingen": has_clingen,
    }
    if enrichment is not None:
        flags["enriched"] = True
        matched = enrichment.get("matched")
        if matched is None:
            matched = enrichment.get("clingen_variation_id_match")
        flags["matched"] = bool(matched)
        flags["providers"] = list(enrichment.get("providers") or [])
    else:
        flags["enriched"] = False
        flags["matched"] = None
        flags["providers"] = []
    return flags


def load_benchmark(name: str = "synthetic_v1") -> dict:
    path = os.path.join(FIXTURES_DIR, name + ".json")
    if not os.path.exists(path):
        raise SystemExit(
            f"Benchmark '{name}' not found at {path}. Run build_fixtures.py first."
        )
    with open(path) as f:
        return json.load(f)


def evaluate(benchmark: dict) -> list:
    results = []
    for case in benchmark["cases"]:
        cls = classify_signals(case["signals"])
        expected, predicted = case["expected"], cls.tier
        result = {
            "id": case["id"],
            "gene": case["gene"],
            "ancestry": case["ancestry"],
            "group_kind": grouping_kind(case["ancestry"]),
            "expected": expected,
            "predicted": predicted,
            "points": cls.total_points,
            "match": expected == predicted,
            "serious": _is_serious(expected, predicted),
        }
        result.update(case_evidence(case))
        results.append(result)
    return results


def _concordance(rows: list) -> float:
    return sum(1 for r in rows if r["match"]) / len(rows) if rows else 0.0


def class_recall(results: list) -> dict:
    """Recall per evidence class (pathogenic / benign / vus).

    Recall = fraction of cases whose *expected* class was reproduced exactly.
    Pathogenic groups Pathogenic + Likely Pathogenic; benign groups Benign +
    Likely Benign; VUS is the single VUS tier (its exact-match rate).
    """
    out = {}
    for name, tiers in _CLASSES.items():
        sub = [r for r in results if r["expected"] in tiers]
        matched = sum(1 for r in sub if r["match"])
        out[name] = {
            "n": len(sub),
            "matched": matched,
            "recall": matched / len(sub) if sub else 0.0,
        }
    return out


def matched_unmatched_concordance(results: list):
    """Concordance split for enriched fixtures: matched vs unmatched cases.

    A case is *matched* when its evidence was found by any enrichment route
    (``enrichment.matched``), falling back to the historical direct Variation ID
    flag for older fixtures. *Unmatched* cases are in the fixture but lack the
    evidence that would let the engine reproduce the label.
    Returns ``None`` for fixtures without any enrichment metadata (the split is
    not applicable there).
    """
    if not any(r.get("enriched") for r in results):
        return None

    def _block(rows):
        definitive = [r for r in rows if r["expected"] != "VUS"]
        return {
            "n": len(rows),
            "concordance": _concordance(rows),
            "definitive_n": len(definitive),
            "definitive_concordance": _concordance(definitive),
            "serious": sum(1 for r in rows if r["serious"]),
        }

    matched = [r for r in results if r.get("matched")]
    unmatched = [r for r in results if r.get("enriched") and not r.get("matched")]
    return {"matched": _block(matched), "unmatched": _block(unmatched)}


def coverage_from_results(results: list) -> dict:
    """Evidence-coverage counts + criteria-count histogram from scored results.

    Mirrors ``compare_reports.evidence_coverage`` (reusing its bucket shape) but
    reads the per-case flags attached during :func:`evaluate`, adding a
    per-provider (ClinGen) column.
    """
    cov = {
        "cases": 0,
        "with_criteria": 0,
        "with_revel": 0,
        "with_gnomad_af": 0,
        "with_clingen": 0,
        "with_enrichment": 0,
        "criteria_buckets": {"0": 0, "1-2": 0, "3-4": 0, "5+": 0},
    }
    for r in results:
        cov["cases"] += 1
        if r.get("n_criteria"):
            cov["with_criteria"] += 1
        if r.get("has_revel"):
            cov["with_revel"] += 1
        if r.get("has_gnomad_af"):
            cov["with_gnomad_af"] += 1
        if r.get("has_clingen"):
            cov["with_clingen"] += 1
        if r.get("enriched"):
            cov["with_enrichment"] += 1
        cov["criteria_buckets"][_cr._criteria_bucket(r.get("n_criteria", 0))] += 1
    return cov


def provider_coverage(results: list) -> dict:
    """Per-provider concordance with vs without each evidence source.

    For every provider (ClinGen criteria plus REVEL / gnomAD AF driven purely by
    fixture-signal presence, not live provider imports) this reports concordance for
    cases that *have* the source vs those that lack it, the improvement delta, and
    a per-class concordance breakdown -- answering which sources improved which
    classes.
    """
    out = {}
    for prov, flag in _PROVIDER_FLAGS.items():
        present = [r for r in results if r.get(flag)]
        absent = [r for r in results if not r.get(flag)]
        present_conc = _concordance(present)
        block = {
            "present_n": len(present),
            "present_concordance": present_conc,
            "present_serious": sum(1 for r in present if r["serious"]),
            "absent_n": len(absent),
            "absent_concordance": _concordance(absent),
            "concordance_delta": present_conc - _concordance(absent),
            "by_class": {},
        }
        for cname, tiers in _CLASSES.items():
            sub = [r for r in present if r["expected"] in tiers]
            block["by_class"][cname] = {
                "n": len(sub),
                "concordance": _concordance(sub),
            }
        out[prov] = block
    return out


def compute_metrics(results: list) -> dict:
    n = len(results)
    definitive = [r for r in results if r["expected"] != "VUS"]
    def_match = sum(1 for r in definitive if r["match"])
    serious = sum(1 for r in results if r["serious"])
    overall = sum(1 for r in results if r["match"])

    by_ancestry: dict = {}
    for r in results:
        a = by_ancestry.setdefault(
            r["ancestry"],
            {"n": 0, "match": 0, "serious": 0, "kind": r.get("group_kind", "unspecified")},
        )
        a["n"] += 1
        a["match"] += int(r["match"])
        a["serious"] += int(r["serious"])
    for a in by_ancestry.values():
        a["concordance"] = a["match"] / a["n"] if a["n"] else 0.0

    metrics = {
        "n": n,
        "definitive_n": len(definitive),
        "definitive_concordance": def_match / len(definitive) if definitive else 0.0,
        "serious_count": serious,
        "serious_rate": serious / n if n else 0.0,
        "overall_concordance": overall / n if n else 0.0,
        "by_ancestry": dict(sorted(by_ancestry.items())),
        "class_recall": class_recall(results),
        "per_tier_recall": _cr.per_tier_recall(results),
        "coverage": coverage_from_results(results),
        "provider_coverage": provider_coverage(results),
        "matched_unmatched": matched_unmatched_concordance(results),
    }
    return metrics


def gate_passes(m: dict) -> bool:
    return (
        m["definitive_concordance"] >= DEFINITIVE_CONCORDANCE_BAR
        and m["serious_rate"] < SERIOUS_DISCORDANCE_BAR
    )


def _confusion(results: list) -> dict:
    matrix = {e: {p: 0 for p in TIERS} for e in TIERS}
    for r in results:
        matrix[r["expected"]][r["predicted"]] += 1
    return matrix


def _pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def _signed_pct(x: float) -> str:
    return f"{x * 100:+.1f} pp"


_PROVIDER_LABELS = {
    "clingen": "ClinGen criteria",
    "revel": "REVEL (PP3/BP4)",
    "gnomad_af": "gnomAD AF (PM2/BA1/BS1)",
}


def _recall_markdown(metrics: dict) -> list:
    """Per-class and per-tier recall tables (tasks 1)."""
    lines = []
    cr = metrics.get("class_recall", {})
    lines.append("## Recall by evidence class")
    lines.append("")
    lines.append("| Class | n | Reproduced | Recall |")
    lines.append("|---|---|---|---|")
    label = {
        "pathogenic": "Pathogenic (P + LP)",
        "benign": "Benign (B + LB)",
        "vus": "VUS (exact match)",
    }
    for key in ("pathogenic", "benign", "vus"):
        b = cr.get(key, {"n": 0, "matched": 0, "recall": 0.0})
        lines.append(f"| {label[key]} | {b['n']} | {b['matched']} | {_pct(b['recall'])} |")
    lines.append("")

    ptr = metrics.get("per_tier_recall", {})
    lines.append("### Per-tier recall (expected tier reproduced exactly)")
    lines.append("")
    lines.append("| Expected tier | n | Reproduced | Recall |")
    lines.append("|---|---|---|---|")
    for tier in TIERS:
        b = ptr.get(tier, {"n": 0, "matched": 0, "recall": 0.0})
        lines.append(f"| {tier} | {b['n']} | {b['matched']} | {_pct(b['recall'])} |")
    lines.append("")
    return lines


def _matched_markdown(metrics: dict) -> list:
    """Matched vs unmatched concordance for enriched fixtures (task 2)."""
    mu = metrics.get("matched_unmatched")
    if not mu:
        return []
    lines = []
    lines.append("## Concordance by evidence match (enriched fixture)")
    lines.append("")
    lines.append("Distinguishes cases whose evidence was found by enrichment "
                 "(*matched*) from cases still missing that evidence (*unmatched*).")
    lines.append("")
    lines.append("| Subset | n | Concordance | Definitive concordance | Serious |")
    lines.append("|---|---|---|---|---|")
    for key, label in (("matched", "Matched (evidence found)"),
                       ("unmatched", "Unmatched (evidence missing)")):
        b = mu[key]
        lines.append(f"| {label} | {b['n']} | {_pct(b['concordance'])} | "
                     f"{_pct(b['definitive_concordance'])} (n={b['definitive_n']}) | "
                     f"{b['serious']} |")
    lines.append("")
    return lines


def _coverage_markdown(metrics: dict) -> list:
    """Evidence-coverage counts + criteria-count buckets (task 3)."""
    cov = metrics.get("coverage")
    if not cov:
        return []
    n = cov["cases"] or 1
    lines = []
    lines.append("## Evidence coverage")
    lines.append("")
    lines.append("| Source present | Cases | Share |")
    lines.append("|---|---|---|")
    rows = [
        ("Any ACMG criteria", "with_criteria"),
        ("ClinGen criteria", "with_clingen"),
        ("REVEL", "with_revel"),
        ("gnomAD AF", "with_gnomad_af"),
        ("Enrichment metadata", "with_enrichment"),
    ]
    for label, key in rows:
        v = cov.get(key, 0)
        lines.append(f"| {label} | {v} | {_pct(v / n)} |")
    lines.append("")
    lines.append("Criteria-count buckets:")
    lines.append("")
    lines.append("| Bucket | Cases |")
    lines.append("|---|---|")
    for bucket in ("0", "1-2", "3-4", "5+"):
        lines.append(f"| {bucket} | {cov['criteria_buckets'].get(bucket, 0)} |")
    lines.append("")
    return lines


def _provider_markdown(metrics: dict) -> list:
    """Per-provider improvement table: which sources improved which classes (task 4)."""
    pc = metrics.get("provider_coverage")
    if not pc:
        return []
    lines = []
    lines.append("## Provider coverage & improvement")
    lines.append("")
    lines.append("Concordance among cases that *have* each source vs those that lack "
                 "it. A positive delta means the source tracks higher concordance. "
                 "REVEL / gnomAD columns are driven by fixture-signal presence.")
    lines.append("")
    lines.append("| Provider | Cases with | Concordance (with) | Concordance (without) "
                 "| Delta | Patho | Benign | VUS |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for prov in ("clingen", "revel", "gnomad_af"):
        b = pc.get(prov)
        if not b:
            continue
        by = b["by_class"]
        lines.append(
            f"| {_PROVIDER_LABELS[prov]} | {b['present_n']} | "
            f"{_pct(b['present_concordance'])} | {_pct(b['absent_concordance'])} | "
            f"{_signed_pct(b['concordance_delta'])} | "
            f"{_pct(by['pathogenic']['concordance'])} (n={by['pathogenic']['n']}) | "
            f"{_pct(by['benign']['concordance'])} (n={by['benign']['n']}) | "
            f"{_pct(by['vus']['concordance'])} (n={by['vus']['n']}) |"
        )
    lines.append("")
    return lines


def _stratification_markdown(metrics: dict) -> list:
    """Concordance split into true ancestry vs VCEP/panel grouping (task 5)."""
    by = metrics.get("by_ancestry", {})
    ancestry = {k: v for k, v in by.items() if v.get("kind") == "ancestry"}
    panel = {k: v for k, v in by.items() if v.get("kind") == "panel"}
    unspecified = {k: v for k, v in by.items() if v.get("kind") == "unspecified"}

    def _table(title, note, groups, label):
        out = [f"## {title}", ""]
        if note:
            out.append(note)
            out.append("")
        if not groups:
            out.append("_No groups of this kind in this benchmark._")
            out.append("")
            return out
        out.append(f"| {label} | n | Concordance | Serious errors |")
        out.append("|---|---|---|---|")
        for name, a in sorted(groups.items()):
            out.append(f"| {name} | {a['n']} | {_pct(a['concordance'])} | {a['serious']} |")
        out.append("")
        return out

    lines = _table(
        "Concordance by ancestry",
        "True genetic-ancestry strata only (VCEP/panel groupings are reported "
        "separately below).",
        ancestry, "Ancestry")
    lines += _table(
        "Concordance by VCEP / panel group",
        "Clinical expert-panel groupings carried in the fixture's `ancestry` "
        "field; these are *not* ancestry strata.",
        panel, "VCEP / panel")
    if unspecified:
        lines += _table(
            "Concordance by unspecified group",
            "Cases whose grouping is neither a recognised ancestry nor a VCEP/panel.",
            unspecified, "Group")
    return lines


def write_reports(benchmark: dict, results: list, metrics: dict, passed: bool) -> None:
    os.makedirs(REPORTS_DIR, exist_ok=True)
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()

    json_payload = {
        "engine_version": C.ENGINE_VERSION,
        "benchmark": benchmark.get("benchmark"),
        "run_utc": now,
        "gate_pass": passed,
        "metrics": metrics,
        "cases": results,
    }
    stem = "validation_report"
    bench = benchmark.get("benchmark")
    suffix = "" if bench in (None, "synthetic_v1") else "_" + bench
    with open(os.path.join(REPORTS_DIR, f"{stem}{suffix}.json"), "w") as f:
        json.dump(json_payload, f, indent=2)
        f.write("\n")

    confusion = _confusion(results)
    lines = []
    lines.append(f"# Validation report -- engine v{C.ENGINE_VERSION}")
    lines.append("")
    lines.append(f"**Gate verdict: {'PASS' if passed else 'FAIL'}**  ")
    lines.append(f"Benchmark: `{benchmark.get('benchmark')}` ({metrics['n']} cases)  ")
    lines.append(f"Run (UTC): {now}")
    lines.append("")
    note = benchmark.get("note") or ""
    if note:
        lines.append("> " + note)
        lines.append("")
    lines.append("## Gate metrics")
    lines.append("")
    lines.append("| Metric | Value | Target | Met |")
    lines.append("|---|---|---|---|")
    lines.append(f"| Concordance on definitive calls | {_pct(metrics['definitive_concordance'])} "
                 f"(n={metrics['definitive_n']}) | >= 85% | "
                 f"{'yes' if metrics['definitive_concordance'] >= DEFINITIVE_CONCORDANCE_BAR else 'no'} |")
    lines.append(f"| Serious discordance (path<->benign) | {_pct(metrics['serious_rate'])} "
                 f"(count={metrics['serious_count']}) | < 1% | "
                 f"{'yes' if metrics['serious_rate'] < SERIOUS_DISCORDANCE_BAR else 'no'} |")
    lines.append(f"| Overall exact-tier concordance | {_pct(metrics['overall_concordance'])} | -- | -- |")
    lines.append("")
    lines.extend(_recall_markdown(metrics))
    lines.extend(_matched_markdown(metrics))
    lines.extend(_coverage_markdown(metrics))
    lines.extend(_provider_markdown(metrics))
    lines.extend(_stratification_markdown(metrics))
    lines.append("## Confusion matrix (expected -> predicted)")
    lines.append("")
    lines.append("| expected \\ predicted | " + " | ".join(TIERS) + " |")
    lines.append("|" + "---|" * (len(TIERS) + 1))
    for e in TIERS:
        row = " | ".join(str(confusion[e][p]) for p in TIERS)
        lines.append(f"| {e} | {row} |")
    lines.append("")
    lines.append("## Per-case detail")
    lines.append("")
    lines.append("| Case | Gene | Ancestry | Expected | Predicted | Points | Match | Serious |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for r in results:
        match = "y" if r["match"] else "."
        serious = "Y" if r["serious"] else "."
        pts = int(r["points"]) if float(r["points"]).is_integer() else r["points"]
        lines.append(f"| {r['id']} | {r['gene']} | {r['ancestry']} | {r['expected']} | "
                     f"{r['predicted']} | {pts} | {match} | {serious} |")
    lines.append("")
    lines.append("---")
    lines.append(f"*Generated by validation/harness.py from fixture "
                 f"`{benchmark.get('benchmark')}`.*")

    with open(os.path.join(REPORTS_DIR, f"{stem}{suffix}.md"), "w") as f:
        f.write("\n".join(lines) + "\n")


def _emit_plots(benchmark: dict, results: list, metrics: dict, passed: bool) -> None:
    """Best-effort diagnostic plots after each run; never block the gate on them."""
    report = {
        "benchmark": benchmark.get("benchmark"),
        "metrics": metrics,
        "cases": results,
        "gate_pass": passed,
    }
    try:
        from validation import plots
    except Exception:  # pragma: no cover - import path fallback when run as a script
        try:
            import plots  # type: ignore
        except Exception as exc:
            print(f"(plots skipped: {exc}; install matplotlib to enable diagnostics)")
            return
    try:
        written = plots.generate_for_report(report)
        plots.generate_summary()
        rel = os.path.relpath(plots.PLOTS_DIR, plots.PROJECT_ROOT)
        print(f"Diagnostic plots: wrote {len(written)} figure(s) to {rel}/")
    except Exception as exc:  # pragma: no cover - plotting must never fail the gate
        print(f"(plots skipped: {exc})")


def main(argv: list | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    name = argv[0] if argv else "synthetic_v1"
    benchmark = load_benchmark(name)
    results = evaluate(benchmark)
    metrics = compute_metrics(results)
    passed = gate_passes(metrics)
    write_reports(benchmark, results, metrics, passed)
    _emit_plots(benchmark, results, metrics, passed)

    print(f"Engine version:            {C.ENGINE_VERSION}")
    print(f"Benchmark:                 {benchmark.get('benchmark')} ({metrics['n']} cases)")
    print(f"Definitive concordance:    {_pct(metrics['definitive_concordance'])} "
          f"(n={metrics['definitive_n']}, bar >= 85%)")
    print(f"Serious discordance:       {_pct(metrics['serious_rate'])} "
          f"(count={metrics['serious_count']}, bar < 1%)")
    print(f"Overall exact concordance: {_pct(metrics['overall_concordance'])}")
    cr = metrics["class_recall"]
    print(f"Class recall:              patho={_pct(cr['pathogenic']['recall'])} "
          f"(n={cr['pathogenic']['n']}), benign={_pct(cr['benign']['recall'])} "
          f"(n={cr['benign']['n']}), VUS={_pct(cr['vus']['recall'])} "
          f"(n={cr['vus']['n']})")
    mu = metrics.get("matched_unmatched")
    if mu:
        print(f"Matched vs unmatched:      matched={_pct(mu['matched']['concordance'])} "
              f"(n={mu['matched']['n']}), unmatched={_pct(mu['unmatched']['concordance'])} "
              f"(n={mu['unmatched']['n']})")
    pc = metrics.get("provider_coverage", {})
    print("Provider concordance delta (with - without):")
    for prov in ("clingen", "revel", "gnomad_af"):
        b = pc.get(prov)
        if b and b["present_n"]:
            print(f"  - {_PROVIDER_LABELS[prov]:24s} with={b['present_n']:>5}  "
                  f"delta={_signed_pct(b['concordance_delta'])}")
    anc_groups = {k: v for k, v in metrics["by_ancestry"].items()
                  if v.get("kind") == "ancestry"}
    if anc_groups:
        print("Per-ancestry concordance (true ancestry):")
        for anc, a in sorted(anc_groups.items()):
            print(f"  - {anc:14s} n={a['n']:>3}  {_pct(a['concordance']):>6}  "
                  f"serious={a['serious']}")
    print()
    print(f"GATE: {'PASS' if passed else 'FAIL'}")
    return 0 if passed else 2


if __name__ == "__main__":
    sys.exit(main())
