"""``reclass`` command-line interface (gap.md D3).

A thin, dependency-free CLI over the pure scoring engine, the validation harness, and
the operator-facing reference/validation tools, so the documented operations are real
commands instead of ``python -m ...`` snippets (plan.md §9). Subcommands:

    reclass classify           -- score a single variant from signals/criteria -> tier
    reclass validate           -- run the validation harness on a fixture, return gate
    reclass reference status   -- report the local GRCh38 reference-cache status
    reclass compare A B        -- before/after diff of two validation reports
    reclass calibration FX     -- calibration + threshold-sensitivity on a fixture
    reclass report analytical-validation -- regenerate the analytical-validation report
    reclass report failures FX -- serious-discordance failure drill-down for a fixture

Each command wraps existing functionality (``engine.reference_cache``,
``validation.compare_reports``, ``validation.calibration``, ``validation.harness``,
``validation.analytical_validation``, ``validation.analyze_failures``) rather than
reimplementing it, and offers ``--json`` machine output where a structured result is
meaningful. Heavy modules are imported lazily inside their command so the ``classify``
path stays import-light.

Installed as the ``reclass`` console entry point (see pyproject.toml); also runnable
in-tree as ``../.venv/bin/python cli.py ...``. The classify path is a pure function of
its inputs (same flags -> same tier and ``reconstruction_hash``), mirroring the engine
contract.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

# Make the sibling packages (engine, validation, ...) importable whether this runs
# in-tree (`python cli.py`) or as an installed console script.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from engine.scoring import classify_signals  # noqa: E402


def _parse_criterion(spec: str) -> dict:
    """Parse ``CRITERION:DIRECTION[:STRENGTH]`` (e.g. ``PVS1:pathogenic:very_strong``)."""
    parts = spec.split(":")
    if len(parts) < 2:
        raise argparse.ArgumentTypeError(
            f"criterion {spec!r} must be CRITERION:DIRECTION[:STRENGTH]")
    crit: dict = {"criterion": parts[0], "direction": parts[1]}
    if len(parts) >= 3 and parts[2]:
        crit["strength"] = parts[2]
    return crit


def _build_signals(args: argparse.Namespace) -> dict:
    signals: dict = {}
    if args.signals_json:
        signals.update(json.loads(args.signals_json))
    if args.revel is not None:
        signals["revel"] = args.revel
    if args.alphamissense is not None:
        signals["alphamissense"] = args.alphamissense
    if args.conservation is not None:
        signals["conservation"] = args.conservation
    if args.gnomad_af is not None:
        signals["gnomad_af"] = args.gnomad_af
    if args.criterion:
        signals.setdefault("criteria", [])
        signals["criteria"].extend(_parse_criterion(c) for c in args.criterion)
    return signals


def _cmd_classify(args: argparse.Namespace) -> int:
    signals = _build_signals(args)
    clf = classify_signals(signals, engine_version=args.engine_version)
    if args.json:
        print(json.dumps(clf.to_dict(), indent=2, sort_keys=True))
        return 0
    print(f"Tier:              {clf.tier}")
    print(f"Total points:      {clf.total_points}")
    print(f"Engine version:    {clf.engine_version}")
    print(f"Reconstruction:    {clf.reconstruction_hash}")
    if clf.contributions:
        print("Contributions:")
        for c in clf.contributions:
            sign = "+" if c.points >= 0 else "-"
            print(f"  {sign} {c.acmg_criterion:6s} {c.evidence_direction:10s} "
                  f"{(c.applied_strength or '-'):12s} ({c.points:+.1f})  [{c.source}]")
    if clf.overrides:
        print("Overrides:")
        for o in clf.overrides:
            print(f"  - {o}")
    return 0


def _cmd_validate(args: argparse.Namespace) -> int:
    from validation import harness  # lazy: only needed for `validate`
    return harness.main([args.fixture])


def _cmd_reference_status(args: argparse.Namespace) -> int:
    # Thin wrap of `python -m engine.reference_cache --status`; that path reports the
    # configured FASTA, loadable state, recorded provenance (source/version) and the
    # checksum/metadata match, and exits 0 even when the cache is absent (a status
    # query succeeding is success, whether or not the genome is installed).
    from engine import reference_cache  # lazy: pulls in the reference loader
    argv = ["--status"]
    if args.json:
        argv.append("--json")
    return reference_cache.main(argv)


def _surface_systemexit(exc: SystemExit) -> int:
    """Surface a wrapped module's ``SystemExit`` as a clean CLI exit code.

    The validation tools raise ``SystemExit`` with a human-readable hint when a
    required report/fixture is missing. Preserve a numeric code as-is; otherwise
    print the message to stderr and fail with exit code 1.
    """
    code = exc.code
    if isinstance(code, int):
        return code
    if code is not None:
        print(str(code), file=sys.stderr)
    return 1


def _cmd_compare(args: argparse.Namespace) -> int:
    from validation import compare_reports  # lazy: only needed for `compare`
    try:
        comparison = compare_reports.run(args.before, args.after)
    except SystemExit as exc:  # missing/unparseable report -> readable, non-zero exit
        return _surface_systemexit(exc)
    if args.json:
        print(json.dumps(comparison, indent=2, sort_keys=True))
        return 0
    model_dir = os.path.dirname(os.path.abspath(__file__))
    print(compare_reports.render_stdout_summary(comparison))
    print("")
    print(f"Wrote: {os.path.relpath(comparison['_md_path'], model_dir)}")
    print(f"Wrote: {os.path.relpath(comparison['_json_path'], model_dir)}")
    return 0


def _cmd_calibration(args: argparse.Namespace) -> int:
    from validation import calibration  # lazy: only needed for `calibration`
    try:
        analysis = calibration.run(args.fixture, run_sensitivity=not args.no_sensitivity)
    except SystemExit as exc:  # missing fixture -> readable, non-zero exit
        return _surface_systemexit(exc)
    if args.json:
        print(json.dumps(analysis, indent=2, sort_keys=True))
        return 0
    model_dir = os.path.dirname(os.path.abspath(__file__))
    print(calibration.render_stdout_summary(analysis))
    print("")
    print(f"Wrote: {os.path.relpath(analysis['_md_path'], model_dir)}")
    print(f"Wrote: {os.path.relpath(analysis['_json_path'], model_dir)}")
    return 0


def _cmd_report_analytical_validation(args: argparse.Namespace) -> int:
    # Wrap validation.analytical_validation (job: validation reporting). Regenerates
    # validation/reports/analytical_validation.{md,json} from a single command, so the
    # operator reaches the same artifact through `reclass` instead of `python -m ...`.
    from validation import analytical_validation  # lazy: only needed for this report
    invocation = " ".join([sys.executable, "-m", "validation.analytical_validation"])
    try:
        report = analytical_validation.run(args.benchmark, invocation=invocation)
    except SystemExit as exc:  # missing fixture -> readable, non-zero exit
        return _surface_systemexit(exc)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0
    model_dir = os.path.dirname(os.path.abspath(__file__))
    print("Analytical validation report")
    print(f"  engine version: {report['engine_version']}")
    print(f"  config hash:    {report['config_hash']}")
    print(f"  benchmarks:     {', '.join(b['benchmark'] for b in report['benchmarks'])}")
    print(f"  clinical state: {report['clinical_release_state']} (not signed off)")
    print("")
    print(f"Wrote: {os.path.relpath(report['_md_path'], model_dir)}")
    print(f"Wrote: {os.path.relpath(report['_json_path'], model_dir)}")
    return 0


def _cmd_report_failures(args: argparse.Namespace) -> int:
    # Wrap validation.analyze_failures (job: validation reporting). Drills down every
    # serious-discordance case for a fixture and writes failure_analysis_<fixture>.{md,json}.
    from validation import analyze_failures  # lazy: only needed for this report
    try:
        analysis = analyze_failures.run(args.fixture)
    except SystemExit as exc:  # missing report/fixture -> readable, non-zero exit
        return _surface_systemexit(exc)
    if args.json:
        print(json.dumps(analysis, indent=2, sort_keys=True))
        return 0
    model_dir = os.path.dirname(os.path.abspath(__file__))
    print(analyze_failures.render_stdout_summary(analysis))
    print("")
    print(f"Wrote: {os.path.relpath(analysis['_md_path'], model_dir)}")
    print(f"Wrote: {os.path.relpath(analysis['_json_path'], model_dir)}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="reclass", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    pc = sub.add_parser("classify", help="score a single variant from signals/criteria")
    pc.add_argument("--revel", type=float, help="REVEL score (0..1)")
    pc.add_argument("--alphamissense", type=float, help="AlphaMissense score (0..1)")
    pc.add_argument("--conservation", type=float, help="phyloP conservation score")
    pc.add_argument("--gnomad-af", dest="gnomad_af", type=float, help="gnomAD popmax allele frequency")
    pc.add_argument("--criterion", action="append", metavar="CRIT:DIR[:STRENGTH]",
                    help="pre-mapped criterion, e.g. PVS1:pathogenic:very_strong (repeatable)")
    pc.add_argument("--signals-json", help="raw signals dict as a JSON string (merged first)")
    pc.add_argument("--engine-version", default=None, help="override the recorded engine version")
    pc.add_argument("--json", action="store_true", help="emit the full classification as JSON")
    pc.set_defaults(func=_cmd_classify)

    pv = sub.add_parser("validate", help="run the validation harness on a fixture")
    pv.add_argument("fixture", help="benchmark name, e.g. synthetic_v1 / clingen_real_v1")
    pv.set_defaults(func=_cmd_validate)

    pr = sub.add_parser("reference", help="inspect the local reference-genome cache")
    pr_sub = pr.add_subparsers(dest="reference_command", required=True)
    prs = pr_sub.add_parser("status", help="report local GRCh38 reference-cache status")
    prs.add_argument("--json", action="store_true", help="emit the status report as JSON")
    prs.set_defaults(func=_cmd_reference_status)

    pcmp = sub.add_parser("compare", help="diff two validation reports (before vs after)")
    pcmp.add_argument("before", help="baseline benchmark name, e.g. clinvar_real_v1")
    pcmp.add_argument("after", help="comparison benchmark name, e.g. clinvar_enriched_v1")
    pcmp.add_argument("--json", action="store_true", help="emit the full comparison as JSON")
    pcmp.set_defaults(func=_cmd_compare)

    pcal = sub.add_parser("calibration", help="calibration + threshold sensitivity on a fixture")
    pcal.add_argument("fixture", help="benchmark name, e.g. clingen_real_v1")
    pcal.add_argument("--no-sensitivity", dest="no_sensitivity", action="store_true",
                      help="skip the threshold-sensitivity re-scoring sweep")
    pcal.add_argument("--json", action="store_true", help="emit the calibration analysis as JSON")
    pcal.set_defaults(func=_cmd_calibration)

    prep = sub.add_parser("report", help="regenerate validation reports (analytical + failures)")
    prep_sub = prep.add_subparsers(dest="report_command", required=True)

    pav = prep_sub.add_parser(
        "analytical-validation",
        help="regenerate the analytical-validation report (md + json)")
    pav.add_argument("--benchmark", action="append", dest="benchmark", metavar="NAME",
                     help="benchmark to include; repeatable (default: all validation fixtures)")
    pav.add_argument("--json", action="store_true", help="emit the full report as JSON")
    pav.set_defaults(func=_cmd_report_analytical_validation)

    pfail = prep_sub.add_parser(
        "failures",
        help="serious-discordance failure drill-down for a fixture (md + json)")
    pfail.add_argument("fixture", help="benchmark name, e.g. clingen_real_v1 / clinvar_enriched_v1")
    pfail.add_argument("--json", action="store_true", help="emit the full analysis as JSON")
    pfail.set_defaults(func=_cmd_report_failures)

    return parser


def main(argv: "list[str] | None" = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
