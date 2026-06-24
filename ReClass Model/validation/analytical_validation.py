#!/usr/bin/env python3
"""Generate the formal analytical-validation report from validation fixtures.

Run from ``ReClass Model/``:

    ../.venv/bin/python -m validation.analytical_validation

The report is a reproducible engineering artifact. It computes metrics from the
checked-in fixtures, imports the harness/compare-report metric helpers, and writes:

    validation/reports/analytical_validation.md
    validation/reports/analytical_validation.json
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import config as C  # noqa: E402
from engine.scoring import classify_signals  # noqa: E402

try:
    from validation import compare_reports as CR  # noqa: E402
    from validation import harness as H  # noqa: E402
except Exception:  # pragma: no cover - script-dir fallback
    import compare_reports as CR  # type: ignore  # noqa: E402
    import harness as H  # type: ignore  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
REPORTS_DIR = os.path.join(HERE, "reports")
FIXTURES_DIR = os.path.join(HERE, "fixtures")

CLINICAL_RELEASE_STATE = "governance_reviewed_pending_credentialed_signoff"
DEFAULT_BENCHMARKS = (
    "synthetic_v1",
    "clingen_real_v1",
    "clinvar_real_v1",
    "clinvar_enriched_v1",
)

KNOWN_LIMITATIONS = [
    "This is analytical validation of deterministic scoring behavior, not clinical validation on an "
    "independent cohort.",
    "Current clinical-release state is not signed off; no output is patient-facing.",
    "ClinVar-derived fixtures include public-label and evidence-completeness limitations.",
    "Provider coverage is measured from fixture signals, not from live upstream provider availability.",
    "Data licensing, regulatory pathway, production deployment, and credentialed lab sign-off are excluded scopes.",
    "Acceptance criteria for future clinical use must be pre-registered by qualified clinical and regulatory owners.",
]


def _model_dir():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_fixture(name, fixtures_dir=None):
    fixtures_dir = fixtures_dir or FIXTURES_DIR
    path = os.path.join(fixtures_dir, name + ".json")
    if not os.path.exists(path):
        raise SystemExit(f"Fixture not found for benchmark '{name}': {path}")
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _counter_rows(counter):
    return [{"name": name, "count": count} for name, count in counter.most_common()]


def fixture_source_versions(fixture):
    """Summarize fixture/source versions without reaching outside fixture JSON."""
    criteria_sources = Counter()
    af_sources = Counter()
    provenance_sources = Counter()
    provider_names = Counter()

    for case in fixture.get("cases", []) or []:
        signals = case.get("signals", {}) or {}
        for criterion in signals.get("criteria", []) or []:
            source = criterion.get("source", "curated")
            version = criterion.get("version") or criterion.get("source_version") or "unspecified"
            criteria_sources[f"{source}:{version}"] += 1
        if signals.get("_af_source"):
            af_sources[str(signals["_af_source"])] += 1
        provenance = case.get("provenance") or {}
        if provenance.get("source"):
            provenance_sources[str(provenance["source"])] += 1
        enrichment = case.get("enrichment") or {}
        for provider in enrichment.get("providers") or []:
            provider_names[str(provider)] += 1

    summary = fixture.get("enrichment_summary") or {}
    return {
        "benchmark": fixture.get("benchmark"),
        "fixture_engine_version": fixture.get("engine_version"),
        "source_file": fixture.get("source_file"),
        "note": fixture.get("note"),
        "enrichment_source": summary.get("source"),
        "enrichment_provider": summary.get("provider"),
        "enrichment_source_versions": {
            k: summary.get(k)
            for k in (
                "source",
                "provider",
                "clingen_variation_id_matches",
                "match_by_variation_id",
                "match_by_canonical_snv_key",
                "match_by_reference_indel_key",
                "match_by_hgvs_g",
                "matched_total",
                "unmatched",
                "ambiguous",
                "normalization_failed",
                "route_counts",
                "label_disagreements",
            )
            if k in summary
        },
        "criteria_source_versions": _counter_rows(criteria_sources),
        "allele_frequency_sources": _counter_rows(af_sources),
        "provenance_sources": _counter_rows(provenance_sources),
        "enrichment_providers": _counter_rows(provider_names),
    }


def reproducibility_check(fixture, *, sample_limit=5):
    """Prove identical fixture evidence reconstructs identical hashes."""
    checked = 0
    mismatches = []
    errors = []
    samples = []
    for case in fixture.get("cases", []) or []:
        checked += 1
        try:
            first = classify_signals(case.get("signals", {}) or {})
            second = classify_signals(case.get("signals", {}) or {})
        except Exception as exc:  # pragma: no cover - defensive reporting path
            errors.append({"id": case.get("id"), "error": str(exc)})
            continue
        same = (
            first.reconstruction_hash == second.reconstruction_hash
            and first.tier == second.tier
            and first.total_points == second.total_points
        )
        if not same:
            mismatches.append({
                "id": case.get("id"),
                "first_hash": first.reconstruction_hash,
                "second_hash": second.reconstruction_hash,
                "first_tier": first.tier,
                "second_tier": second.tier,
            })
        if len(samples) < sample_limit:
            samples.append({
                "id": case.get("id"),
                "tier": first.tier,
                "total_points": first.total_points,
                "reconstruction_hash": first.reconstruction_hash,
            })
    return {
        "checked_cases": checked,
        "mismatch_count": len(mismatches),
        "error_count": len(errors),
        "passed": not mismatches and not errors,
        "mismatches": mismatches[:20],
        "errors": errors[:20],
        "sample_hashes": samples,
        "assertion": "identical fixture evidence reconstructed identical classification hashes",
    }


def _scope_value(scope, result, case):
    case = case or {}
    if scope == "vcep":
        return case.get("vcep_group") or case.get("vcep") or (
            result.get("ancestry") if result.get("group_kind") == "panel" else None
        )
    if scope == "gene":
        return result.get("gene") or case.get("gene")
    if scope == "disease":
        return case.get("disease") or case.get("disease_id") or case.get("condition")
    if scope == "population":
        return case.get("population") or case.get("population_group") or (
            result.get("ancestry") if result.get("group_kind") == "ancestry" else None
        )
    if scope == "variant_class":
        return case.get("variant_class") or case.get("variant_type") or case.get("molecular_class")
    return None


def _scope_gate_block(rows):
    n = len(rows)
    definitive = [row for row in rows if row.get("expected") != "VUS"]
    definitive_match = sum(1 for row in definitive if row.get("match"))
    serious = sum(1 for row in rows if row.get("serious"))
    block = {
        "n": n,
        "definitive_n": len(definitive),
        "definitive_concordance": definitive_match / len(definitive) if definitive else 0.0,
        "serious_count": serious,
        "serious_rate": serious / n if n else 0.0,
    }
    block["gate_pass"] = H.gate_passes(block)
    return block


def scoped_validation_gates(fixture, results=None):
    """Validation gates by VCEP, gene, disease, population, and variant class."""
    results = results or H.evaluate(fixture)
    cases_by_id = {case.get("id"): case for case in fixture.get("cases", []) or []}
    grouped = {scope: {} for scope in ("vcep", "gene", "disease", "population", "variant_class")}
    for result in results:
        case = cases_by_id.get(result.get("id"), {})
        for scope in grouped:
            value = _scope_value(scope, result, case)
            if value in (None, ""):
                continue
            grouped[scope].setdefault(str(value), []).append(result)

    out = {}
    for scope, values in grouped.items():
        blocks = []
        for value, rows in values.items():
            block = _scope_gate_block(rows)
            block["scope_type"] = scope
            block["scope_value"] = value
            blocks.append(block)
        blocks.sort(key=lambda row: (row["gate_pass"], -row["serious_count"], row["scope_value"]))
        out[scope] = blocks
    return out


def analyze_benchmark(fixture):
    """Analyze one fixture using shared validation metric helpers."""
    results = H.evaluate(fixture)
    metrics = H.compute_metrics(results)
    return {
        "benchmark": fixture.get("benchmark"),
        "case_count": len(fixture.get("cases", []) or []),
        "gate_pass": H.gate_passes(metrics),
        "metrics": metrics,
        "confusion_matrix": CR.confusion_matrix(results),
        "fixture_source_versions": fixture_source_versions(fixture),
        "reproducibility_check": reproducibility_check(fixture),
        "scoped_gates": scoped_validation_gates(fixture, results),
    }


def validation_report_id(payload):
    """Stable id for the validation metrics artifact, independent of generation time."""
    stable = {
        "engine_version": payload.get("engine_version"),
        "config_hash": payload.get("config_hash"),
        "benchmarks": [
            {
                "benchmark": bench.get("benchmark"),
                "case_count": bench.get("case_count"),
                "gate_pass": bench.get("gate_pass"),
                "metrics": bench.get("metrics"),
                "fixture_source_versions": bench.get("fixture_source_versions"),
            }
            for bench in payload.get("benchmarks", [])
        ],
    }
    digest = hashlib.sha256(
        json.dumps(stable, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()
    return f"analytical-validation-{digest[:16]}"


def command_manifest(benchmarks, invocation=None):
    harness_commands = []
    for name in benchmarks:
        suffix = "" if name == "synthetic_v1" else f" {name}"
        harness_commands.append(f"../.venv/bin/python validation/harness.py{suffix}")
    benchmark_args = []
    if tuple(benchmarks) != DEFAULT_BENCHMARKS:
        for name in benchmarks:
            benchmark_args.extend(["--benchmark", name])
    analytical_command = "../.venv/bin/python -m validation.analytical_validation"
    if benchmark_args:
        analytical_command += " " + " ".join(benchmark_args)
    return {
        "analytical_validation": analytical_command,
        "observed_invocation": invocation,
        "equivalent_harness_commands": harness_commands,
        "serious_discordance_drill_down": [
            "../.venv/bin/python -m validation.analyze_failures clingen_real_v1",
            "../.venv/bin/python -m validation.analyze_failures clinvar_enriched_v1",
        ],
        "verification": [
            "../.venv/bin/python -m unittest discover -s tests -v",
            "../.venv/bin/python -m ruff check validation tests",
            "../.venv/bin/python -m mypy",
        ],
    }


def build_report(benchmarks=None, *, fixtures_dir=None, invocation=None):
    names = list(benchmarks or DEFAULT_BENCHMARKS)
    fixture_payloads = [load_fixture(name, fixtures_dir=fixtures_dir) for name in names]
    benchmark_reports = [analyze_benchmark(fixture) for fixture in fixture_payloads]
    fp = C.config_fingerprint()
    report = {
        "generated_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "engine_version": C.ENGINE_VERSION,
        "config_hash": fp.get("config_hash"),
        "config_fingerprint": fp,
        "clinical_release_state": CLINICAL_RELEASE_STATE,
        "clinical_release_signed_off": False,
        "clinical_release_statement": (
            f"Clinical-release state is {CLINICAL_RELEASE_STATE}; analytical validation is not "
            "credentialed clinical sign-off."
        ),
        "commands": command_manifest(names, invocation=invocation),
        "benchmarks": benchmark_reports,
        "known_limitations_and_excluded_scopes": list(KNOWN_LIMITATIONS),
    }
    report["validation_report_id"] = validation_report_id(report)
    return report


def _pct(value):
    try:
        return f"{float(value) * 100.0:.1f}%"
    except (TypeError, ValueError):
        return "--"


def _fmt_bool(value):
    return "yes" if value else "no"


def _markdown_table(rows, columns):
    lines = ["| " + " | ".join(label for label, _key in columns) + " |"]
    lines.append("|" + "---|" * len(columns))
    for row in rows:
        lines.append("| " + " | ".join(str(row.get(key, "--")) for _label, key in columns) + " |")
    return lines


def _confusion_markdown(matrix):
    lines = ["| expected \\ predicted | " + " | ".join(H.TIERS) + " |"]
    lines.append("|" + "---|" * (len(H.TIERS) + 1))
    for expected in H.TIERS:
        row = matrix.get(expected, {})
        lines.append("| %s | %s |" % (
            expected,
            " | ".join(str(row.get(predicted, 0)) for predicted in H.TIERS),
        ))
    return lines


def _class_recall_markdown(metrics):
    recall = metrics.get("class_recall", {})
    labels = {
        "pathogenic": "Pathogenic / likely pathogenic",
        "benign": "Benign / likely benign",
        "vus": "VUS",
    }
    rows = []
    for key in ("pathogenic", "benign", "vus"):
        block = recall.get(key, {})
        rows.append({
            "class": labels[key],
            "n": block.get("n", 0),
            "matched": block.get("matched", 0),
            "recall": _pct(block.get("recall", 0.0)),
        })
    return _markdown_table(rows, [
        ("Class", "class"),
        ("n", "n"),
        ("Reproduced", "matched"),
        ("Recall", "recall"),
    ])


def _provider_markdown(metrics):
    coverage = metrics.get("provider_coverage", {})
    labels = {
        "clingen": "ClinGen criteria",
        "revel": "REVEL",
        "gnomad_af": "gnomAD AF",
    }
    rows = []
    for key in ("clingen", "revel", "gnomad_af"):
        block = coverage.get(key, {})
        rows.append({
            "provider": labels[key],
            "present_n": block.get("present_n", 0),
            "present_concordance": _pct(block.get("present_concordance", 0.0)),
            "absent_n": block.get("absent_n", 0),
            "absent_concordance": _pct(block.get("absent_concordance", 0.0)),
            "delta": f"{block.get('concordance_delta', 0.0) * 100.0:+.1f} pp",
        })
    return _markdown_table(rows, [
        ("Provider", "provider"),
        ("Cases with", "present_n"),
        ("Concordance with", "present_concordance"),
        ("Cases without", "absent_n"),
        ("Concordance without", "absent_concordance"),
        ("Delta", "delta"),
    ])


def _stratification_markdown(metrics, *, limit=30):
    groups = metrics.get("by_ancestry", {}) or {}
    rows = []
    for name, block in sorted(groups.items(), key=lambda item: (item[1].get("kind", ""), item[0])):
        rows.append({
            "group": name,
            "kind": block.get("kind", "unspecified"),
            "n": block.get("n", 0),
            "concordance": _pct(block.get("concordance", 0.0)),
            "serious": block.get("serious", 0),
        })
    if len(rows) > limit:
        rows = rows[:limit] + [{
            "group": f"...{len(groups) - limit} more groups in JSON",
            "kind": "",
            "n": "",
            "concordance": "",
            "serious": "",
        }]
    return _markdown_table(rows, [
        ("Group", "group"),
        ("Kind", "kind"),
        ("n", "n"),
        ("Concordance", "concordance"),
        ("Serious", "serious"),
    ])


def _source_version_markdown(source_versions):
    rows = [
        {"field": "Fixture engine version", "value": source_versions.get("fixture_engine_version") or "--"},
        {"field": "Source file", "value": source_versions.get("source_file") or "--"},
        {"field": "Enrichment provider", "value": source_versions.get("enrichment_provider") or "--"},
    ]
    lines = _markdown_table(rows, [("Field", "field"), ("Value", "value")])
    criteria = source_versions.get("criteria_source_versions") or []
    if criteria:
        lines += ["", "Criteria source versions:"]
        lines += _markdown_table(criteria, [("Source/version", "name"), ("Criteria", "count")])
    af_sources = source_versions.get("allele_frequency_sources") or []
    if af_sources:
        lines += ["", "Allele-frequency sources:"]
        lines += _markdown_table(af_sources, [("Source", "name"), ("Cases", "count")])
    return lines


def _scoped_gates_markdown(scoped_gates, *, limit=25):
    labels = {
        "vcep": "VCEP",
        "gene": "Gene",
        "disease": "Disease",
        "population": "Population",
        "variant_class": "Variant class",
    }
    rows = []
    for scope in ("vcep", "gene", "disease", "population", "variant_class"):
        for block in (scoped_gates.get(scope) or [])[:limit]:
            rows.append({
                "scope": labels[scope],
                "value": block["scope_value"],
                "n": block["n"],
                "definitive": _pct(block["definitive_concordance"]),
                "serious": block["serious_count"],
                "gate": "PASS" if block["gate_pass"] else "FAIL",
            })
    if not rows:
        return ["_No scoped fields were present in this fixture._"]
    return _markdown_table(rows, [
        ("Scope", "scope"),
        ("Value", "value"),
        ("n", "n"),
        ("Definitive concordance", "definitive"),
        ("Serious", "serious"),
        ("Gate", "gate"),
    ])


def render_markdown(report):
    lines = []
    w = lines.append
    w("# Analytical validation report")
    w("")
    w(f"Generated (UTC): {report['generated_utc']}")
    w("")
    w(f"**{report['clinical_release_statement']}**")
    w("")
    w("## Engine and configuration")
    w("")
    w("| Field | Value |")
    w("|---|---|")
    w(f"| Engine version | `{report['engine_version']}` |")
    w(f"| Config hash | `{report['config_hash']}` |")
    w(f"| Validation report id | `{report['validation_report_id']}` |")
    w(f"| Clinical-release signed off | {_fmt_bool(report['clinical_release_signed_off'])} |")
    w("")

    w("## Exact commands")
    w("")
    commands = report["commands"]
    w(f"- Analytical validation: `{commands['analytical_validation']}`")
    if commands.get("observed_invocation"):
        w(f"- Observed invocation: `{commands['observed_invocation']}`")
    for cmd in commands["equivalent_harness_commands"]:
        w(f"- Equivalent harness metric source: `{cmd}`")
    for cmd in commands["serious_discordance_drill_down"]:
        w(f"- Serious-discordance drill-down: `{cmd}`")
    w("")

    w("## Benchmark summary")
    w("")
    summary_rows = []
    for bench in report["benchmarks"]:
        metrics = bench["metrics"]
        summary_rows.append({
            "benchmark": bench["benchmark"],
            "cases": metrics.get("n"),
            "definitive": _pct(metrics.get("definitive_concordance")),
            "overall": _pct(metrics.get("overall_concordance")),
            "serious": metrics.get("serious_count"),
            "gate": "PASS" if bench.get("gate_pass") else "FAIL",
            "repro": "PASS" if bench["reproducibility_check"]["passed"] else "FAIL",
        })
    lines += _markdown_table(summary_rows, [
        ("Benchmark", "benchmark"),
        ("Cases", "cases"),
        ("Definitive concordance", "definitive"),
        ("Overall concordance", "overall"),
        ("Serious", "serious"),
        ("Gate", "gate"),
        ("Reproducibility", "repro"),
    ])
    w("")

    for bench in report["benchmarks"]:
        metrics = bench["metrics"]
        repro = bench["reproducibility_check"]
        w(f"## `{bench['benchmark']}`")
        w("")
        w("### Fixture and source versions")
        w("")
        lines += _source_version_markdown(bench["fixture_source_versions"])
        w("")

        w("### Gate metrics")
        w("")
        rows = [
            {"metric": "Cases", "value": metrics.get("n")},
            {
                "metric": "Definitive concordance",
                "value": f"{_pct(metrics.get('definitive_concordance'))} (n={metrics.get('definitive_n')})",
            },
            {"metric": "Overall exact concordance", "value": _pct(metrics.get("overall_concordance"))},
            {
                "metric": "Serious pathogenic/benign discordance",
                "value": f"{metrics.get('serious_count')} ({_pct(metrics.get('serious_rate'))})",
            },
        ]
        lines += _markdown_table(rows, [("Metric", "metric"), ("Value", "value")])
        w("")

        w("### Scoped validation gates")
        w("")
        lines += _scoped_gates_markdown(bench.get("scoped_gates", {}))
        w("")

        w("### Sensitivity-style recall by class")
        w("")
        lines += _class_recall_markdown(metrics)
        w("")

        w("### Confusion matrix")
        w("")
        lines += _confusion_markdown(bench["confusion_matrix"])
        w("")

        w("### Provider coverage")
        w("")
        lines += _provider_markdown(metrics)
        w("")

        w("### Stratification")
        w("")
        lines += _stratification_markdown(metrics)
        w("")

        matched = metrics.get("matched_unmatched")
        if matched:
            w("### Matched vs unmatched evidence")
            w("")
            rows = []
            for key in ("matched", "unmatched"):
                block = matched[key]
                rows.append({
                    "subset": key,
                    "n": block["n"],
                    "concordance": _pct(block["concordance"]),
                    "definitive": _pct(block["definitive_concordance"]),
                    "serious": block["serious"],
                })
            lines += _markdown_table(rows, [
                ("Subset", "subset"),
                ("n", "n"),
                ("Concordance", "concordance"),
                ("Definitive concordance", "definitive"),
                ("Serious", "serious"),
            ])
            w("")

        w("### Reproducibility check")
        w("")
        w(
            f"Checked {repro['checked_cases']} cases; hash mismatches={repro['mismatch_count']}; "
            f"errors={repro['error_count']}; result={'PASS' if repro['passed'] else 'FAIL'}."
        )
        w("")
        if repro["sample_hashes"]:
            lines += _markdown_table(repro["sample_hashes"], [
                ("Case", "id"),
                ("Tier", "tier"),
                ("Points", "total_points"),
                ("Reconstruction hash", "reconstruction_hash"),
            ])
            w("")

    w("## Known limitations and excluded scopes")
    w("")
    for item in report["known_limitations_and_excluded_scopes"]:
        w(f"- {item}")
    w("")
    w("---")
    w("*Generated by validation/analytical_validation.py.*")
    return "\n".join(lines).rstrip() + "\n"


def run(benchmarks=None, *, model_dir=None, invocation=None):
    model_dir = model_dir or _model_dir()
    fixtures_dir = os.path.join(model_dir, "validation", "fixtures")
    reports_dir = os.path.join(model_dir, "validation", "reports")
    report = build_report(benchmarks, fixtures_dir=fixtures_dir, invocation=invocation)

    os.makedirs(reports_dir, exist_ok=True)
    md_path = os.path.join(reports_dir, "analytical_validation.md")
    json_path = os.path.join(reports_dir, "analytical_validation.json")
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write(render_markdown(report))
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)
        fh.write("\n")
    report["_md_path"] = md_path
    report["_json_path"] = json_path
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description="Generate analytical-validation reports.")
    parser.add_argument(
        "--benchmark",
        action="append",
        dest="benchmarks",
        help="benchmark to include; repeatable (default: all validation fixtures)",
    )
    args = parser.parse_args(argv)
    invocation = " ".join([sys.executable, *sys.argv])
    report = run(args.benchmarks, invocation=invocation)
    print("Analytical validation report")
    print(f"  engine version: {report['engine_version']}")
    print(f"  config hash:    {report['config_hash']}")
    print(f"  benchmarks:     {', '.join(b['benchmark'] for b in report['benchmarks'])}")
    print(f"  clinical state: {report['clinical_release_state']} (not signed off)")
    print("")
    print(f"Wrote: {os.path.relpath(report['_md_path'], _model_dir())}")
    print(f"Wrote: {os.path.relpath(report['_json_path'], _model_dir())}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
