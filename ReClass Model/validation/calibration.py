#!/usr/bin/env python3
"""Calibration & threshold-sensitivity reporting by VCEP / gene / disease group.

The point model is only clinically reviewable if you can see *where* it agrees and
disagrees with expert calls and *how sensitive* that agreement is to the configured
thresholds. This tool turns a benchmark into a calibration report (roadmap §2, tasks
5-6):

  * **Per-group calibration** -- concordance, definitive concordance, and serious
    (pathogenic<->benign) discordance for each VCEP/panel group and for each gene, so
    a systematically weaker group is never hidden inside a pooled number.
  * **Low-performing-group triage** -- groups below the definitive-concordance bar
    (or with any serious discordance) are ranked and surfaced explicitly.
  * **Threshold-sensitivity analysis** -- re-scores the benchmark under perturbed
    versioned configs (shifted tier cutoffs / allele-frequency thresholds / REVEL
    bins) and reports how concordance and serious-discordance move, so a reviewer can
    see how brittle a threshold choice is *before* changing it.
  * **Serious-discordance review** -- every pathogenic<->benign error listed with the
    evidence that produced it.

Pure analysis functions (``group_metrics``, ``low_performing_groups``,
``threshold_sensitivity``, ...) take plain dicts so they unit-test on tiny in-memory
benchmarks. ``run`` adds path resolution and writes
``validation/reports/calibration_<name>.{md,json}``. PS4 cohort thresholds are out of
scope for this tool and live with reanalysis/cohort logic in
``monitoring/reanalysis.py``.

Run from ``ReClass Model/``::

    ../.venv/bin/python validation/calibration.py clingen_real_v1
    ../.venv/bin/python validation/calibration.py clinvar_enriched_v1
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.scoring import classify_signals  # noqa: E402
from engine import config as C  # noqa: E402
from engine.config_registry import get_config  # noqa: E402
from reporting.reviewer import build_validation_review_packet  # noqa: E402

try:
    from validation import fixture_splits as FS  # noqa: E402
    from validation import harness as H  # noqa: E402
except Exception:  # pragma: no cover - script-dir fallback
    import fixture_splits as FS  # type: ignore  # noqa: E402
    import harness as H  # type: ignore  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURES_DIR = os.path.join(HERE, "fixtures")
REPORTS_DIR = os.path.join(HERE, "reports")

TIERS = H.TIERS
_PATHO = H._PATHO
_BENIGN = H._BENIGN

# A group must reach this many cases before its concordance is treated as a signal
# (smaller groups are reported but not flagged as "low-performing" on noise).
DEFAULT_MIN_GROUP_N = 20
DEFAULT_CONCORDANCE_BAR = H.DEFINITIVE_CONCORDANCE_BAR  # 0.85


# --------------------------------------------------------------------------- #
# Pure metric blocks                                                          #
# --------------------------------------------------------------------------- #
def _is_serious(expected: str, predicted: str) -> bool:
    return H._is_serious(expected, predicted)


def _block(rows: list) -> dict:
    """Concordance block for a list of scored result rows."""
    n = len(rows)
    definitive = [r for r in rows if r["expected"] != "VUS"]
    def_match = sum(1 for r in definitive if r["match"])
    return {
        "n": n,
        "concordance": (sum(1 for r in rows if r["match"]) / n) if n else 0.0,
        "definitive_n": len(definitive),
        "definitive_concordance": (def_match / len(definitive)) if definitive else 0.0,
        "serious": sum(1 for r in rows if r["serious"]),
    }


def group_metrics(results: list, key: str, kind_field: str = None) -> list:
    """Per-group concordance blocks keyed by a result field (e.g. ``gene``).

    Returns a list of blocks (one per distinct key value) sorted by serious
    discordance desc, then definitive concordance asc, then n desc -- so the most
    concerning groups sort to the top.
    """
    groups: dict = {}
    kinds: dict = {}
    for r in results:
        k = r.get(key)
        groups.setdefault(k, []).append(r)
        if kind_field and k not in kinds:
            kinds[k] = r.get(kind_field)
    out = []
    for k, rows in groups.items():
        b = _block(rows)
        b["group"] = k
        if kind_field:
            b["kind"] = kinds.get(k)
        out.append(b)
    out.sort(key=lambda b: (-b["serious"], b["definitive_concordance"], -b["n"]))
    return out


def low_performing_groups(
    blocks: list,
    *,
    min_n: int = DEFAULT_MIN_GROUP_N,
    bar: float = DEFAULT_CONCORDANCE_BAR,
) -> list:
    """Groups that warrant triage: enough cases AND (below the bar OR any serious).

    Serious discordances are flagged regardless of group size; concordance is only
    judged once a group has ``min_n`` definitive calls so a 2-case group is not
    paraded as a failure.
    """
    flagged = []
    for b in blocks:
        big_enough = b["definitive_n"] >= min_n
        below_bar = big_enough and b["definitive_concordance"] < bar
        if below_bar or b["serious"] > 0:
            reasons = []
            if below_bar:
                reasons.append(
                    f"definitive concordance {b['definitive_concordance']*100:.1f}% "
                    f"< {bar*100:.0f}% (n={b['definitive_n']})"
                )
            if b["serious"] > 0:
                reasons.append(f"{b['serious']} serious pathogenic<->benign discordance(s)")
            fb = dict(b)
            fb["reasons"] = reasons
            flagged.append(fb)
    return flagged


# --------------------------------------------------------------------------- #
# Threshold-sensitivity analysis (task 6)                                     #
# --------------------------------------------------------------------------- #
def score_under_config(benchmark: dict, config) -> dict:
    """Score every case under a versioned ``config`` and return summary metrics.

    ``config=None`` uses the base engine config (identical to the harness).
    """
    FS.assert_not_holdout(benchmark, purpose="threshold sensitivity")
    n = serious = overall = def_n = def_match = 0
    for case in benchmark.get("cases", []):
        expected = case["expected"]
        predicted = classify_signals(case["signals"], config=config).tier
        match = expected == predicted
        n += 1
        overall += int(match)
        if _is_serious(expected, predicted):
            serious += 1
        if expected != "VUS":
            def_n += 1
            def_match += int(match)
    return {
        "n": n,
        "overall_concordance": overall / n if n else 0.0,
        "definitive_n": def_n,
        "definitive_concordance": def_match / def_n if def_n else 0.0,
        "serious_count": serious,
        "serious_rate": serious / n if n else 0.0,
    }


def default_perturbations(base=None) -> list:
    """A standard set of (name, description, config) threshold perturbations.

    Each perturbs exactly one knob off the base config so the sensitivity is
    attributable. Configs are non-base, so each carries a fingerprinted
    ``engine_version`` (a config-relevant change alters reconstruction hashes).
    """
    base = base or get_config()
    cuts = list(base.tier_cutoffs)

    def shift_cutoffs(delta):
        # Shift only the pathogenic-side cutoffs (Pathogenic / Likely Pathogenic).
        out = []
        for thr, tier in cuts:
            out.append((thr + delta, tier) if tier in _PATHO else (thr, tier))
        return tuple(out)

    return [
        ("pathogenic_cutoffs_+2",
         "Require 2 more points for Pathogenic / Likely Pathogenic (stricter).",
         base.perturb(tier_cutoffs=shift_cutoffs(+2), version_suffix="-pcut+2")),
        ("pathogenic_cutoffs_-2",
         "Require 2 fewer points for Pathogenic / Likely Pathogenic (looser).",
         base.perturb(tier_cutoffs=shift_cutoffs(-2), version_suffix="-pcut-2")),
        ("pm2_10x_stricter",
         "Lower the PM2 rarity threshold 10x (fewer PM2 calls).",
         base.perturb(pm2_af=base.pm2_af / 10.0, version_suffix="-pm2/10")),
        ("bs1_2x_looser",
         "Double the BS1 frequency threshold (fewer BS1 calls).",
         base.perturb(bs1_af=base.bs1_af * 2.0, version_suffix="-bs1x2")),
        ("ba1_half",
         "Halve the BA1 stand-alone-benign frequency threshold (more BA1 calls).",
         base.perturb(ba1_af=base.ba1_af / 2.0, version_suffix="-ba1/2")),
    ]


def threshold_sensitivity(benchmark: dict, perturbations=None) -> dict:
    """Re-score under base + each perturbation; report metric deltas vs base."""
    FS.assert_not_holdout(benchmark, purpose="threshold sensitivity")
    base_metrics = score_under_config(benchmark, None)
    rows = []
    for name, desc, cfg in (perturbations or default_perturbations()):
        m = score_under_config(benchmark, cfg)
        rows.append({
            "name": name,
            "description": desc,
            "engine_version": cfg.engine_version,
            "metrics": m,
            "definitive_concordance_delta": m["definitive_concordance"]
            - base_metrics["definitive_concordance"],
            "serious_count_delta": m["serious_count"] - base_metrics["serious_count"],
            "overall_concordance_delta": m["overall_concordance"]
            - base_metrics["overall_concordance"],
        })
    return {"base": base_metrics, "perturbations": rows}


# --------------------------------------------------------------------------- #
# Serious-discordance review (task 6)                                         #
# --------------------------------------------------------------------------- #
def serious_discordances(results: list) -> list:
    """Every pathogenic<->benign discordance, with direction and evidence flags."""
    out = []
    for r in results:
        if not r["serious"]:
            continue
        direction = ("pathogenic_called_benign"
                     if r["expected"] in _PATHO else "benign_called_pathogenic")
        out.append({
            "id": r["id"],
            "gene": r["gene"],
            "group": r["ancestry"],
            "expected": r["expected"],
            "predicted": r["predicted"],
            "points": r["points"],
            "direction": direction,
            "n_criteria": r.get("n_criteria"),
            "has_revel": r.get("has_revel"),
            "has_gnomad_af": r.get("has_gnomad_af"),
            "has_clingen": r.get("has_clingen"),
        })
    out.sort(key=lambda d: (d["direction"], str(d["gene"]), str(d["id"])))
    return out


def _classification_packet_dict(case: dict) -> dict:
    cls = classify_signals(case.get("signals", {}) or {})
    return {
        "tier": cls.tier,
        "total_points": cls.total_points,
        "engine_version": cls.engine_version,
        "reconstruction_hash": cls.reconstruction_hash,
        "overrides": list(cls.overrides),
        "variant_key": case.get("variant_key"),
    }


def review_packets_for_serious_discordances(benchmark: dict, results: list | None = None) -> list:
    """Build machine-readable reviewer packets for serious calibration cases."""
    results = results or H.evaluate(benchmark)
    cases_by_id = {case.get("id"): case for case in benchmark.get("cases", []) or []}
    packets = []
    for result in results:
        if not result.get("serious"):
            continue
        case = cases_by_id.get(result.get("id"))
        if not case:
            continue
        packets.append(
            build_validation_review_packet(
                benchmark=benchmark.get("benchmark"),
                case=case,
                result=result,
                classification=_classification_packet_dict(case),
                root_cause_category="calibration_serious_discordance",
                proposed_remediation=(
                    "Credentialed reviewer must adjudicate the pathogenic-vs-benign "
                    "discordance before release."
                ),
            )
        )
    return packets


# --------------------------------------------------------------------------- #
# Top-level analysis                                                          #
# --------------------------------------------------------------------------- #
def calibrate(benchmark: dict, *, run_sensitivity: bool = True) -> dict:
    """Build the full calibration analysis from a benchmark dict."""
    FS.assert_not_holdout(benchmark, purpose="calibration")
    results = H.evaluate(benchmark)
    overall = _block(results)

    by_vcep = [b for b in group_metrics(results, "ancestry", kind_field="group_kind")
               if b.get("kind") == "panel"]
    by_ancestry = [b for b in group_metrics(results, "ancestry", kind_field="group_kind")
                   if b.get("kind") == "ancestry"]
    by_gene = group_metrics(results, "gene")

    analysis = {
        "benchmark": benchmark.get("benchmark"),
        "engine_version": C.ENGINE_VERSION,
        "config_fingerprint": C.config_fingerprint(),
        "overall": overall,
        "by_vcep": by_vcep,
        "by_ancestry": by_ancestry,
        "by_gene": by_gene,
        "low_performing_vcep": low_performing_groups(by_vcep),
        "low_performing_genes": low_performing_groups(by_gene),
        "serious_discordances": serious_discordances(results),
        "review_packets": review_packets_for_serious_discordances(benchmark, results),
    }
    if run_sensitivity:
        analysis["threshold_sensitivity"] = threshold_sensitivity(benchmark)
    return analysis


# --------------------------------------------------------------------------- #
# Rendering                                                                   #
# --------------------------------------------------------------------------- #
def _pct(x) -> str:
    try:
        return f"{100.0 * float(x):.1f}%"
    except (TypeError, ValueError):
        return "--"


def _signed_pct(x) -> str:
    if not isinstance(x, (int, float)):
        return "--"
    return f"{100.0 * x:+.1f} pp"


def _signed_int(x) -> str:
    if not isinstance(x, (int, float)):
        return "--"
    return f"{int(x):+d}"


def _group_table(title, note, blocks, label, limit=25) -> list:
    out = [f"## {title}", ""]
    if note:
        out += [note, ""]
    if not blocks:
        return out + ["_No groups of this kind in this benchmark._", ""]
    out.append(f"| {label} | n | Definitive n | Definitive concordance | Overall | Serious |")
    out.append("|---|---|---|---|---|---|")
    for b in blocks[:limit]:
        out.append(
            f"| {b['group']} | {b['n']} | {b['definitive_n']} | "
            f"{_pct(b['definitive_concordance'])} | {_pct(b['concordance'])} | {b['serious']} |"
        )
    if len(blocks) > limit:
        out.append(f"| _...{len(blocks) - limit} more groups_ | | | | | |")
    out.append("")
    return out


def render_markdown(a: dict) -> str:
    out = []
    w = out.append
    w(f"# Calibration report -- `{a['benchmark']}`")
    w("")
    fp = a.get("config_fingerprint", {})
    w(f"Engine `{a.get('engine_version')}`  |  config `{fp.get('config_hash', '')[:12]}`  "
      f"|  overrides: {', '.join(str(x) for x in fp.get('override_ids', []) if x) or 'none'}")
    w("")
    o = a["overall"]
    w("## Overall")
    w("")
    w("| Metric | Value |")
    w("|---|---|")
    w(f"| Cases | {o['n']} |")
    w(f"| Definitive concordance | {_pct(o['definitive_concordance'])} (n={o['definitive_n']}) |")
    w(f"| Overall exact concordance | {_pct(o['concordance'])} |")
    w(f"| Serious discordances | {o['serious']} |")
    w("")

    out += _group_table(
        "Calibration by VCEP / panel group",
        "Clinical expert-panel groupings (the fixture's `ancestry` field). Sorted "
        "with the most concerning groups first.",
        a["by_vcep"], "VCEP / panel")
    out += _group_table(
        "Calibration by gene",
        "Per-gene concordance; sorted by serious discordance then concordance.",
        a["by_gene"], "Gene")
    if a.get("by_ancestry"):
        out += _group_table(
            "Calibration by ancestry",
            "True genetic-ancestry strata (where the benchmark carries them).",
            a["by_ancestry"], "Ancestry")

    # Low-performing triage ------------------------------------------------- #
    w("## Low-performing groups (triage)")
    w("")
    lp = (a.get("low_performing_vcep") or []) + (a.get("low_performing_genes") or [])
    if not lp:
        w("_No groups fell below the calibration bar or showed serious discordance._")
    else:
        w(f"Bar: definitive concordance >= {_pct(DEFAULT_CONCORDANCE_BAR)} "
          f"(judged at n >= {DEFAULT_MIN_GROUP_N}); any serious discordance is flagged "
          "regardless of size.")
        w("")
        w("| Group | n | Definitive concordance | Serious | Why flagged |")
        w("|---|---|---|---|---|")
        for b in lp[:40]:
            w(f"| {b['group']} | {b['n']} | {_pct(b['definitive_concordance'])} | "
              f"{b['serious']} | {'; '.join(b['reasons'])} |")
    w("")

    # Threshold sensitivity ------------------------------------------------- #
    ts = a.get("threshold_sensitivity")
    if ts:
        base = ts["base"]
        w("## Threshold-sensitivity analysis")
        w("")
        w(f"Base: definitive concordance {_pct(base['definitive_concordance'])}, "
          f"serious {base['serious_count']}, overall {_pct(base['overall_concordance'])}. "
          "Each row perturbs ONE threshold off the base config and re-scores.")
        w("")
        w("| Perturbation | Definitive concordance | Δ | Serious | Δ | Overall | Δ |")
        w("|---|---|---|---|---|---|---|")
        for r in ts["perturbations"]:
            m = r["metrics"]
            w(f"| {r['name']} | {_pct(m['definitive_concordance'])} | "
              f"{_signed_pct(r['definitive_concordance_delta'])} | {m['serious_count']} | "
              f"{_signed_int(r['serious_count_delta'])} | {_pct(m['overall_concordance'])} | "
              f"{_signed_pct(r['overall_concordance_delta'])} |")
        w("")
        for r in ts["perturbations"]:
            w(f"- `{r['name']}` ({r['engine_version']}): {r['description']}")
        w("")

    # Serious-discordance review -------------------------------------------- #
    sd = a.get("serious_discordances", [])
    w(f"## Serious pathogenic<->benign discordances ({len(sd)})")
    w("")
    if not sd:
        w("_None._")
    else:
        w("| Case | Gene | Group | Expected | Predicted | Points | Direction | "
          "Criteria | REVEL | gnomAD |")
        w("|---|---|---|---|---|---|---|---|---|---|")
        for d in sd[:200]:
            w(f"| {d['id']} | {d['gene']} | {d['group']} | {d['expected']} | "
              f"{d['predicted']} | {d['points']} | {d['direction']} | {d.get('n_criteria')} | "
              f"{'y' if d.get('has_revel') else '.'} | {'y' if d.get('has_gnomad_af') else '.'} |")
        if len(sd) > 200:
            w(f"| _...{len(sd) - 200} more_ | | | | | | | | | |")
    w("")
    w("---")
    w(f"*Generated by validation/calibration.py from fixture `{a['benchmark']}`.*")
    return "\n".join(out).rstrip() + "\n"


def render_stdout_summary(a: dict) -> str:
    o = a["overall"]
    lines = [
        f"Calibration: {a['benchmark']}",
        f"  definitive concordance {_pct(o['definitive_concordance'])} "
        f"(n={o['definitive_n']}), serious {o['serious']}",
        f"  VCEP/panel groups: {len(a['by_vcep'])}, genes: {len(a['by_gene'])}",
    ]
    lp = (a.get("low_performing_vcep") or []) + (a.get("low_performing_genes") or [])
    lines.append(f"  low-performing groups flagged: {len(lp)}")
    if lp:
        for b in lp[:5]:
            lines.append(f"    - {b['group']}: {'; '.join(b['reasons'])}")
    ts = a.get("threshold_sensitivity")
    if ts:
        lines.append("  threshold sensitivity (definitive concordance delta):")
        for r in ts["perturbations"]:
            lines.append(f"    - {r['name']:24s} {_signed_pct(r['definitive_concordance_delta'])}  "
                         f"serious {_signed_int(r['serious_count_delta'])}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# IO / orchestration                                                          #
# --------------------------------------------------------------------------- #
def load_benchmark(name: str) -> dict:
    path = os.path.join(FIXTURES_DIR, name + ".json")
    if not os.path.exists(path):
        raise SystemExit(f"Benchmark '{name}' not found at {path}.")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def run(name: str, *, run_sensitivity: bool = True) -> dict:
    benchmark = load_benchmark(name)
    analysis = calibrate(benchmark, run_sensitivity=run_sensitivity)
    analysis["generated_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()

    os.makedirs(REPORTS_DIR, exist_ok=True)
    md_path = os.path.join(REPORTS_DIR, f"calibration_{name}.md")
    json_path = os.path.join(REPORTS_DIR, f"calibration_{name}.json")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(render_markdown(analysis))
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(analysis, f, indent=2)
        f.write("\n")
    analysis["_md_path"] = md_path
    analysis["_json_path"] = json_path
    return analysis


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Calibration & threshold-sensitivity report by VCEP/gene/disease.")
    parser.add_argument("benchmark", help="benchmark name, e.g. clingen_real_v1")
    parser.add_argument("--no-sensitivity", action="store_true",
                        help="skip the threshold-sensitivity re-scoring sweep")
    args = parser.parse_args(argv)
    analysis = run(args.benchmark, run_sensitivity=not args.no_sensitivity)
    print(render_stdout_summary(analysis))
    print("")
    print(f"Wrote: {os.path.relpath(analysis['_md_path'], os.path.dirname(HERE))}")
    print(f"Wrote: {os.path.relpath(analysis['_json_path'], os.path.dirname(HERE))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
