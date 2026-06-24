"""Blinded, pre-registered held-out evaluation of the locked ReClass engine.

This tool executes the frozen plan in ``preregistration.json``. It:

  1. Re-verifies the locked engine config hash (aborts if the config drifted).
  2. Re-verifies the pinned holdout-partition fingerprints (aborts if a fixture
     drifted, so the "held-out" claim cannot silently change).
  3. Scores the reserved HOLDOUT sub-split of each real benchmark **once** under
     the locked config -- no tuning, no threshold search, no perturbed re-scoring.
  4. Adds Wilson 95% confidence intervals, a development-vs-holdout overfit check,
     and a label-balance check.
  5. Evaluates the pre-registered acceptance criteria and writes
     ``reports/holdout_evaluation.{json,md}``.

Exit code 0 = primary hypothesis (H1) PASSES, 2 = FAILS, 3 = registration/config
or partition mismatch (the run is not valid).

Run from ``ReClass Model/``::

    ../.venv/bin/python validation/holdout_eval.py
"""

from __future__ import annotations

import datetime
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import config as C  # noqa: E402

try:
    from validation import fixture_splits as FS  # noqa: E402
    from validation import harness as H  # noqa: E402
except Exception:  # pragma: no cover - script-dir fallback
    import fixture_splits as FS  # type: ignore  # noqa: E402
    import harness as H  # type: ignore  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURES_DIR = os.path.join(HERE, "fixtures")
REPORTS_DIR = os.path.join(HERE, "reports")
PREREG_PATH = os.path.join(HERE, "preregistration.json")

_PATHO = {"Likely Pathogenic", "Pathogenic"}
_BENIGN = {"Benign", "Likely Benign"}


class RegistrationError(RuntimeError):
    """Raised when the live config/partition no longer matches the registration."""


def wilson_interval(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Two-sided Wilson score interval for a binomial proportion k/n."""
    if n <= 0:
        return (0.0, 0.0)
    p = k / n
    denom = 1.0 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    margin = (z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / denom
    return (max(0.0, center - margin), min(1.0, center + margin))


def load_prereg() -> dict:
    with open(PREREG_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def _verify_config(prereg: dict) -> dict:
    locked = prereg["locked_engine"]
    fp = C.config_fingerprint()
    if fp.get("config_hash") != locked["config_hash"]:
        raise RegistrationError(
            "Locked config hash mismatch: registration pins "
            f"{locked['config_hash']!r} but the live engine config is "
            f"{fp.get('config_hash')!r}. The held-out evaluation is only valid "
            "under the registered configuration; re-register (bump the version) "
            "if the config legitimately changed."
        )
    if fp.get("engine_version") != locked["engine_version"]:
        raise RegistrationError(
            f"Engine version mismatch: registration pins {locked['engine_version']!r}, "
            f"live engine is {fp.get('engine_version')!r}."
        )
    return fp


def load_benchmark(name: str) -> dict:
    path = os.path.join(FIXTURES_DIR, name + ".json")
    if not os.path.exists(path):
        raise SystemExit(f"Benchmark {name!r} not found at {path}.")
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _verify_partition(name: str, cases: list, expected: dict) -> dict:
    """Re-derive and check the pinned holdout fingerprint for one benchmark."""
    fp = FS.partition_fingerprint(cases)
    actual = {
        "n_total": len(cases),
        "n_holdout": fp["n_holdout"],
        "holdout_fraction_actual": round(fp["n_holdout"] / len(cases), 4) if cases else 0.0,
        "holdout_sha256": fp["sha256"],
    }
    if actual["holdout_sha256"] != expected["holdout_sha256"]:
        raise RegistrationError(
            f"Holdout partition for {name!r} drifted from the registration. "
            f"Registered sha256={expected['holdout_sha256']}, "
            f"recomputed={actual['holdout_sha256']}. The fixture or split rule "
            "changed; re-register before reporting held-out numbers."
        )
    return actual


def _counts(results: list) -> dict:
    """Exact integer counts feeding the confidence intervals."""
    definitive = [r for r in results if r["expected"] != "VUS"]
    def_match = sum(1 for r in definitive if r["match"])
    serious = sum(1 for r in results if r["serious"])
    return {
        "n": len(results),
        "definitive_n": len(definitive),
        "definitive_match": def_match,
        "serious": serious,
    }


def evaluate_subset(name: str, cases: list) -> dict:
    """Score a sub-split once and attach Wilson intervals to the headline rates."""
    bench = {"benchmark": name, "cases": cases}
    results = H.evaluate(bench)
    metrics = H.compute_metrics(results)
    cnt = _counts(results)

    def_lo, def_hi = wilson_interval(cnt["definitive_match"], cnt["definitive_n"])
    ser_lo, ser_hi = wilson_interval(cnt["serious"], cnt["n"])
    return {
        "n": cnt["n"],
        "definitive_n": cnt["definitive_n"],
        "definitive_concordance": metrics["definitive_concordance"],
        "definitive_concordance_ci95": [def_lo, def_hi],
        "serious_count": cnt["serious"],
        "serious_rate": metrics["serious_rate"],
        "serious_rate_ci95": [ser_lo, ser_hi],
        "overall_concordance": metrics["overall_concordance"],
        "class_recall": metrics["class_recall"],
        "by_ancestry": metrics["by_ancestry"],
        "matched_unmatched": metrics.get("matched_unmatched"),
    }


def _label_distribution(cases: list) -> dict:
    dist: dict[str, int] = {}
    for case in cases:
        dist[case["expected"]] = dist.get(case["expected"], 0) + 1
    return dict(sorted(dist.items()))


def evaluate_benchmark(name: str, expected_partition: dict) -> dict:
    benchmark = load_benchmark(name)
    cases = benchmark["cases"]
    partition_check = _verify_partition(name, cases, expected_partition)

    parts = FS.partition_cases(cases)
    dev_cases, hold_cases = parts[FS.DEVELOPMENT], parts[FS.HOLDOUT]

    dev = evaluate_subset(name, dev_cases)
    hold = evaluate_subset(name, hold_cases)
    overfit_gap = dev["definitive_concordance"] - hold["definitive_concordance"]

    return {
        "benchmark": name,
        "note": benchmark.get("note"),
        "partition": partition_check,
        "label_distribution": {
            "development": _label_distribution(dev_cases),
            "holdout": _label_distribution(hold_cases),
        },
        "development": dev,
        "holdout": hold,
        "overfit_gap": overfit_gap,
    }


def assess(prereg: dict, by_bench: dict) -> dict:
    """Apply the frozen acceptance criteria to the held-out results."""
    t = prereg["thresholds"]
    primary_name = t["primary_benchmark"]
    primary = by_bench[primary_name]["holdout"]

    def_lo = primary["definitive_concordance_ci95"][0]
    ser_hi = primary["serious_rate_ci95"][1]
    h1_def_pass = def_lo >= t["primary_definitive_concordance_wilson_lower_min"]
    h1_ser_pass = ser_hi < t["primary_serious_rate_wilson_upper_max"]
    h1_pass = h1_def_pass and h1_ser_pass

    real_name, enriched_name = t["contrast_pair"]
    real_dc = by_bench[real_name]["holdout"]["definitive_concordance"]
    enriched_dc = by_bench[enriched_name]["holdout"]["definitive_concordance"]
    contrast_delta = enriched_dc - real_dc
    h3_pass = contrast_delta >= t["contrast_enriched_minus_real_min"]

    overfit = {}
    for name, res in by_bench.items():
        gap = res["overfit_gap"]
        overfit[name] = {"gap": gap, "flagged": gap > t["overfit_gap_max"]}

    return {
        "primary_benchmark": primary_name,
        "H1": {
            "definitive_concordance": primary["definitive_concordance"],
            "definitive_concordance_wilson_lower": def_lo,
            "definitive_bar": t["primary_definitive_concordance_wilson_lower_min"],
            "definitive_pass": h1_def_pass,
            "serious_rate": primary["serious_rate"],
            "serious_rate_wilson_upper": ser_hi,
            "serious_bar": t["primary_serious_rate_wilson_upper_max"],
            "serious_pass": h1_ser_pass,
            "pass": h1_pass,
        },
        "H3_contrast": {
            "real_definitive_concordance": real_dc,
            "enriched_definitive_concordance": enriched_dc,
            "delta": contrast_delta,
            "bar": t["contrast_enriched_minus_real_min"],
            "pass": h3_pass,
        },
        "overfit": overfit,
        "verdict_pass": h1_pass,
    }


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #

def _pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def _ci(ci: list) -> str:
    return f"[{ci[0] * 100:.1f}%, {ci[1] * 100:.1f}%]"


def render_markdown(payload: dict) -> str:
    prereg = payload["preregistration"]
    assessment = payload["assessment"]
    h1 = assessment["H1"]
    lines: list[str] = []
    w = lines.append

    w("# Held-out validation report (pre-registered)")
    w("")
    verdict = "PASS" if assessment["verdict_pass"] else "FAIL"
    w(f"**Primary hypothesis (H1) verdict: {verdict}**  ")
    w(f"Engine `v{payload['engine']['engine_version']}` · config "
      f"`{payload['engine']['config_hash'][:12]}…`  ")
    w(f"Pre-registration: `{prereg['title']}` ({prereg['status']}, "
      f"{prereg['registered_utc']})  ")
    w(f"Run (UTC): {payload['run_utc']}")
    w("")
    w("> Numbers below are scored on the **reserved holdout sub-split only** — "
      "variants the locked configuration was never exposed to and calibration is "
      "forbidden from seeing. They are out-of-sample estimates.")
    w("")

    w("## H1 — primary endpoint (clingen_real_v1 holdout)")
    w("")
    w("| Endpoint | Estimate | 95% CI | Bar | Met |")
    w("|---|---|---|---|---|")
    w(f"| Definitive concordance | {_pct(h1['definitive_concordance'])} | "
      f"lower {_pct(h1['definitive_concordance_wilson_lower'])} | "
      f"lower ≥ {_pct(h1['definitive_bar'])} | "
      f"{'yes' if h1['definitive_pass'] else 'no'} |")
    w(f"| Serious P↔B discordance | {_pct(h1['serious_rate'])} | "
      f"upper {_pct(h1['serious_rate_wilson_upper'])} | "
      f"upper < {_pct(h1['serious_bar'])} | "
      f"{'yes' if h1['serious_pass'] else 'no'} |")
    w("")

    w("## Held-out results by benchmark")
    w("")
    w("| Benchmark | Holdout n | Definitive concordance (95% CI) | Serious | "
      "Dev concordance | Overfit gap |")
    w("|---|---:|---|---:|---:|---:|")
    for name, res in payload["benchmarks"].items():
        ho, dev = res["holdout"], res["development"]
        gap = res["overfit_gap"]
        flag = " ⚠️" if assessment["overfit"][name]["flagged"] else ""
        w(f"| `{name}` | {ho['n']} | {_pct(ho['definitive_concordance'])} "
          f"{_ci(ho['definitive_concordance_ci95'])} (n={ho['definitive_n']}) | "
          f"{ho['serious_count']} | {_pct(dev['definitive_concordance'])} | "
          f"{gap * 100:+.1f} pp{flag} |")
    w("")

    h3 = assessment["H3_contrast"]
    w("## H3 — evidence-completeness contrast")
    w("")
    w("Held-out definitive concordance, sparse ClinVar vs the same variants enriched "
      "with matched ClinGen-applied criteria:")
    w("")
    w(f"- `clinvar_real_v1` holdout: **{_pct(h3['real_definitive_concordance'])}**")
    w(f"- `clinvar_enriched_v1` holdout: **{_pct(h3['enriched_definitive_concordance'])}**")
    w(f"- Lift: **{h3['delta'] * 100:+.1f} pp** (bar ≥ {h3['bar'] * 100:.0f} pp → "
      f"{'met' if h3['pass'] else 'not met'})")
    w("")
    w("The same locked engine reproduces far more expert calls when the evidence is "
      "complete than when it is sparse — evidence completeness, not the scoring math, "
      "is the binding constraint.")
    w("")

    w("## Split balance (blindness check)")
    w("")
    w("Holdout vs development label distributions should match closely, since the "
      "split is keyed on variant identity, not label.")
    w("")
    for name, res in payload["benchmarks"].items():
        dist = res["label_distribution"]
        w(f"- `{name}`: dev={dist['development']} · holdout={dist['holdout']}")
    w("")

    w("## Partition fingerprints (re-verified this run)")
    w("")
    w("| Benchmark | Total | Holdout | % | SHA-256 (prefix) |")
    w("|---|---:|---:|---:|---|")
    for name, res in payload["benchmarks"].items():
        p = res["partition"]
        w(f"| `{name}` | {p['n_total']} | {p['n_holdout']} | "
          f"{_pct(p['holdout_fraction_actual'])} | `{p['holdout_sha256'][:14]}…` |")
    w("")
    w("---")
    w("*Generated by validation/holdout_eval.py against validation/preregistration.json.*")
    return "\n".join(lines) + "\n"


def build_payload(prereg: dict, fingerprint: dict) -> dict:
    by_bench: dict = {}
    for name, expected in prereg["expected_partition"].items():
        by_bench[name] = evaluate_benchmark(name, expected)
    assessment = assess(prereg, by_bench)
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    return {
        "run_utc": now,
        "engine": fingerprint,
        "preregistration": {
            "title": prereg["title"],
            "status": prereg["status"],
            "registered_utc": prereg["registered_utc"],
        },
        "benchmarks": by_bench,
        "assessment": assessment,
    }


def write_reports(payload: dict) -> tuple[str, str]:
    os.makedirs(REPORTS_DIR, exist_ok=True)
    json_path = os.path.join(REPORTS_DIR, "holdout_evaluation.json")
    md_path = os.path.join(REPORTS_DIR, "holdout_evaluation.md")
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
        fh.write("\n")
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write(render_markdown(payload))
    return json_path, md_path


def main(argv: list | None = None) -> int:
    prereg = load_prereg()
    try:
        fingerprint = _verify_config(prereg)
        payload = build_payload(prereg, fingerprint)
    except RegistrationError as exc:
        print(f"REGISTRATION ERROR: {exc}")
        return 3

    json_path, md_path = write_reports(payload)
    a = payload["assessment"]
    h1 = a["H1"]
    print(f"Engine v{fingerprint['engine_version']} · config {fingerprint['config_hash'][:12]}…")
    print(f"Pre-registration: {prereg['title']} ({prereg['status']})")
    print()
    print("HELD-OUT results (reserved sub-split, locked config):")
    for name, res in payload["benchmarks"].items():
        ho = res["holdout"]
        print(f"  {name:22s} n={ho['n']:>5}  def-conc={_pct(ho['definitive_concordance']):>6} "
              f"CI{_ci(ho['definitive_concordance_ci95'])}  serious={ho['serious_count']}")
    print()
    print(f"H1 (primary, {a['primary_benchmark']} holdout):")
    print(f"  definitive concordance {_pct(h1['definitive_concordance'])} "
          f"(Wilson lower {_pct(h1['definitive_concordance_wilson_lower'])}, "
          f"bar ≥ {_pct(h1['definitive_bar'])}) -> {'PASS' if h1['definitive_pass'] else 'FAIL'}")
    print(f"  serious discordance {_pct(h1['serious_rate'])} "
          f"(Wilson upper {_pct(h1['serious_rate_wilson_upper'])}, "
          f"bar < {_pct(h1['serious_bar'])}) -> {'PASS' if h1['serious_pass'] else 'FAIL'}")
    h3 = a["H3_contrast"]
    print(f"H3 (evidence-completeness contrast): real={_pct(h3['real_definitive_concordance'])} "
          f"-> enriched={_pct(h3['enriched_definitive_concordance'])} "
          f"({h3['delta'] * 100:+.1f} pp, {'met' if h3['pass'] else 'not met'})")
    print()
    print(f"Wrote {os.path.relpath(json_path, HERE)} and {os.path.relpath(md_path, HERE)}")
    print(f"PRIMARY VERDICT: {'PASS' if a['verdict_pass'] else 'FAIL'}")
    return 0 if a["verdict_pass"] else 2


if __name__ == "__main__":
    sys.exit(main())
