"""Standard-library unit tests for the ``reclass`` CLI (cli.py).

Covers argument parsing/dispatch for every subcommand plus at least one happy path
per command. Heavy work is kept out of the tests:

  * ``reference status`` runs for real -- the status path is designed to exit cleanly
    even when no FASTA cache is installed, so it needs no artifact.
  * ``compare`` and ``calibration`` patch the wrapped module's ``run`` with a result
    computed from a tiny in-memory fixture (via the module's own pure functions), so
    the CLI glue is exercised without resolving committed reports or writing files.

Run from the project root (the ``ReClass Model/`` folder)::

    ../.venv/bin/python -m unittest discover -s tests -v
"""

from __future__ import annotations

import io
import json
import os
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cli  # noqa: E402
from validation import (  # noqa: E402
    analytical_validation,
    analyze_failures,
    calibration,
    compare_reports,
)


def _run(argv: list) -> "tuple[int, str, str]":
    """Invoke ``cli.main(argv)`` capturing (exit_code, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = cli.main(argv)
    return rc, out.getvalue(), err.getvalue()


# --------------------------------------------------------------------------- #
# Parsing / dispatch                                                          #
# --------------------------------------------------------------------------- #
class TestParsing(unittest.TestCase):
    def _parse(self, argv: list):
        return cli.build_parser().parse_args(argv)

    def test_classify_dispatch_unchanged(self):
        args = self._parse(["classify", "--revel", "0.9", "--json"])
        self.assertIs(args.func, cli._cmd_classify)
        self.assertEqual(args.revel, 0.9)
        self.assertTrue(args.json)

    def test_validate_dispatch_unchanged(self):
        args = self._parse(["validate", "clingen_real_v1"])
        self.assertIs(args.func, cli._cmd_validate)
        self.assertEqual(args.fixture, "clingen_real_v1")

    def test_reference_status_dispatch(self):
        args = self._parse(["reference", "status"])
        self.assertIs(args.func, cli._cmd_reference_status)
        self.assertFalse(args.json)
        args = self._parse(["reference", "status", "--json"])
        self.assertTrue(args.json)

    def test_reference_requires_subcommand(self):
        with self.assertRaises(SystemExit):
            self._parse(["reference"])

    def test_compare_dispatch(self):
        args = self._parse(["compare", "clinvar_real_v1", "clinvar_enriched_v1"])
        self.assertIs(args.func, cli._cmd_compare)
        self.assertEqual(args.before, "clinvar_real_v1")
        self.assertEqual(args.after, "clinvar_enriched_v1")
        self.assertFalse(args.json)
        self.assertTrue(self._parse(["compare", "a", "b", "--json"]).json)

    def test_compare_requires_two_positionals(self):
        with self.assertRaises(SystemExit):
            self._parse(["compare", "only_one"])

    def test_calibration_dispatch(self):
        args = self._parse(["calibration", "clingen_real_v1"])
        self.assertIs(args.func, cli._cmd_calibration)
        self.assertEqual(args.fixture, "clingen_real_v1")
        self.assertFalse(args.json)
        self.assertFalse(args.no_sensitivity)
        args = self._parse(["calibration", "fx", "--json", "--no-sensitivity"])
        self.assertTrue(args.json)
        self.assertTrue(args.no_sensitivity)

    def test_report_analytical_validation_dispatch(self):
        args = self._parse(["report", "analytical-validation"])
        self.assertIs(args.func, cli._cmd_report_analytical_validation)
        self.assertIsNone(args.benchmark)
        self.assertFalse(args.json)
        args = self._parse(["report", "analytical-validation",
                            "--benchmark", "a", "--benchmark", "b", "--json"])
        self.assertEqual(args.benchmark, ["a", "b"])
        self.assertTrue(args.json)

    def test_report_failures_dispatch(self):
        args = self._parse(["report", "failures", "clingen_real_v1"])
        self.assertIs(args.func, cli._cmd_report_failures)
        self.assertEqual(args.fixture, "clingen_real_v1")
        self.assertFalse(args.json)
        self.assertTrue(self._parse(["report", "failures", "fx", "--json"]).json)

    def test_report_requires_subcommand(self):
        with self.assertRaises(SystemExit):
            self._parse(["report"])

    def test_unknown_command_errors(self):
        with self.assertRaises(SystemExit):
            self._parse(["bogus"])


# --------------------------------------------------------------------------- #
# classify regression (must keep working exactly as before)                   #
# --------------------------------------------------------------------------- #
class TestClassifyRegression(unittest.TestCase):
    def test_classify_human_output(self):
        rc, out, _ = _run(["classify", "--criterion", "PVS1:pathogenic:very_strong",
                            "--criterion", "PM2:pathogenic:moderate"])
        self.assertEqual(rc, 0)
        self.assertIn("Tier:", out)
        self.assertIn("Pathogenic", out)

    def test_classify_json_output(self):
        rc, out, _ = _run(["classify", "--criterion", "PVS1:pathogenic:very_strong",
                            "--json"])
        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertIn("tier", payload)
        self.assertIn("reconstruction_hash", payload)


# --------------------------------------------------------------------------- #
# reference status (real run; no artifact required)                           #
# --------------------------------------------------------------------------- #
class TestReferenceStatus(unittest.TestCase):
    def test_human_output_runs_without_cache(self):
        rc, out, _ = _run(["reference", "status"])
        self.assertEqual(rc, 0)
        self.assertIn("reference cache status", out)
        self.assertIn("loadable", out)

    def test_json_output_is_valid(self):
        rc, out, _ = _run(["reference", "status", "--json"])
        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertIn("loadable", payload)
        self.assertIn("path", payload)


# --------------------------------------------------------------------------- #
# compare (tiny in-memory fixture via the module's own pure compare())        #
# --------------------------------------------------------------------------- #
def _tiny_comparison() -> dict:
    before = {
        "benchmark": "before_fx", "engine_version": "vA", "gate_pass": True,
        "metrics": {"n": 2, "definitive_n": 2, "definitive_concordance": 0.5,
                    "serious_count": 1, "serious_rate": 0.5, "overall_concordance": 0.5},
        "cases": [
            {"id": "c1", "expected": "Pathogenic", "predicted": "VUS", "match": False},
            {"id": "c2", "expected": "Benign", "predicted": "Benign", "match": True},
        ],
    }
    after = {
        "benchmark": "after_fx", "engine_version": "vB", "gate_pass": True,
        "metrics": {"n": 2, "definitive_n": 2, "definitive_concordance": 1.0,
                    "serious_count": 0, "serious_rate": 0.0, "overall_concordance": 1.0},
        "cases": [
            {"id": "c1", "expected": "Pathogenic", "predicted": "Pathogenic", "match": True},
            {"id": "c2", "expected": "Benign", "predicted": "Benign", "match": True},
        ],
    }
    comparison = compare_reports.compare(
        before, after, before_name="before_fx", after_name="after_fx")
    comparison["_md_path"] = "/tmp/comparison_before_fx_vs_after_fx.md"
    comparison["_json_path"] = "/tmp/comparison_before_fx_vs_after_fx.json"
    return comparison


class TestCompare(unittest.TestCase):
    def test_human_happy_path(self):
        comparison = _tiny_comparison()
        with mock.patch.object(compare_reports, "run", return_value=comparison):
            rc, out, _ = _run(["compare", "before_fx", "after_fx"])
        self.assertEqual(rc, 0)
        self.assertIn("Comparison: before_fx -> after_fx", out)
        self.assertIn("Wrote:", out)

    def test_json_happy_path(self):
        comparison = _tiny_comparison()
        with mock.patch.object(compare_reports, "run", return_value=comparison):
            rc, out, _ = _run(["compare", "before_fx", "after_fx", "--json"])
        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertEqual(payload["before"], "before_fx")
        self.assertIn("metrics", payload)

    def test_missing_report_exits_nonzero(self):
        def _boom(before, after, model_dir=None):
            raise SystemExit("Report not found for benchmark 'ghost'")

        with mock.patch.object(compare_reports, "run", side_effect=_boom):
            rc, _, err = _run(["compare", "ghost", "after_fx"])
        self.assertEqual(rc, 1)
        self.assertIn("Report not found", err)


# --------------------------------------------------------------------------- #
# calibration (tiny in-memory benchmark via the module's own calibrate())     #
# --------------------------------------------------------------------------- #
def _tiny_analysis() -> dict:
    benchmark = {
        "benchmark": "tiny_v1",
        "cases": [
            {"id": "c1", "gene": "BRCA1", "ancestry": "European", "expected": "Pathogenic",
             "signals": {"criteria": [
                 {"criterion": "PVS1", "direction": "pathogenic", "strength": "very_strong"},
                 {"criterion": "PM2", "direction": "pathogenic", "strength": "moderate"}]}},
            {"id": "c2", "gene": "TP53", "ancestry": "European", "expected": "Benign",
             "signals": {"criteria": [
                 {"criterion": "BA1", "direction": "benign", "strength": "stand_alone"}]}},
        ],
    }
    analysis = calibration.calibrate(benchmark, run_sensitivity=False)
    analysis["_md_path"] = "/tmp/calibration_tiny_v1.md"
    analysis["_json_path"] = "/tmp/calibration_tiny_v1.json"
    return analysis


class TestCalibration(unittest.TestCase):
    def test_human_happy_path(self):
        analysis = _tiny_analysis()
        with mock.patch.object(calibration, "run", return_value=analysis):
            rc, out, _ = _run(["calibration", "tiny_v1"])
        self.assertEqual(rc, 0)
        self.assertIn("Calibration: tiny_v1", out)
        self.assertIn("Wrote:", out)

    def test_json_happy_path(self):
        analysis = _tiny_analysis()
        with mock.patch.object(calibration, "run", return_value=analysis):
            rc, out, _ = _run(["calibration", "tiny_v1", "--json"])
        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertEqual(payload["benchmark"], "tiny_v1")
        self.assertIn("overall", payload)

    def test_no_sensitivity_flag_forwarded(self):
        analysis = _tiny_analysis()
        with mock.patch.object(calibration, "run", return_value=analysis) as m:
            rc, _, _ = _run(["calibration", "tiny_v1", "--no-sensitivity"])
        self.assertEqual(rc, 0)
        _, kwargs = m.call_args
        self.assertFalse(kwargs["run_sensitivity"])

    def test_missing_fixture_exits_nonzero(self):
        def _boom(name, *, run_sensitivity=True):
            raise SystemExit("Benchmark 'ghost' not found at /x/ghost.json.")

        with mock.patch.object(calibration, "run", side_effect=_boom):
            rc, _, err = _run(["calibration", "ghost"])
        self.assertEqual(rc, 1)
        self.assertIn("not found", err)


# --------------------------------------------------------------------------- #
# report analytical-validation (wraps validation.analytical_validation)        #
# --------------------------------------------------------------------------- #
def _tiny_av_report() -> dict:
    return {
        "engine_version": "1.0.0",
        "config_hash": "deadbeef",
        "benchmarks": [{"benchmark": "tiny_v1"}],
        "clinical_release_state": "governance_reviewed_pending_credentialed_signoff",
        "_md_path": "/tmp/analytical_validation.md",
        "_json_path": "/tmp/analytical_validation.json",
    }


class TestReportAnalyticalValidation(unittest.TestCase):
    def test_human_happy_path(self):
        with mock.patch.object(analytical_validation, "run",
                               return_value=_tiny_av_report()):
            rc, out, _ = _run(["report", "analytical-validation"])
        self.assertEqual(rc, 0)
        self.assertIn("Analytical validation report", out)
        self.assertIn("not signed off", out)  # never imply clinical approval
        self.assertIn("Wrote:", out)

    def test_json_happy_path(self):
        with mock.patch.object(analytical_validation, "run",
                               return_value=_tiny_av_report()):
            rc, out, _ = _run(["report", "analytical-validation", "--json"])
        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertEqual(payload["engine_version"], "1.0.0")
        self.assertIn("clinical_release_state", payload)

    def test_benchmark_flag_forwarded(self):
        with mock.patch.object(analytical_validation, "run",
                               return_value=_tiny_av_report()) as m:
            rc, _, _ = _run(["report", "analytical-validation", "--benchmark", "tiny_v1"])
        self.assertEqual(rc, 0)
        args, _ = m.call_args
        self.assertEqual(args[0], ["tiny_v1"])  # first positional is the benchmark list


# --------------------------------------------------------------------------- #
# report failures (wraps validation.analyze_failures; real analysis on a       #
# tiny in-memory report+fixture via the module's own pure analyze())           #
# --------------------------------------------------------------------------- #
def _tiny_failure_analysis() -> dict:
    report = {
        "benchmark": "tiny_fail_v1", "engine_version": "vA",
        "cases": [
            {"id": "c1", "gene": "BRCA1", "ancestry": "European",
             "expected": "Pathogenic", "predicted": "Likely Benign",
             "match": False, "serious": True},
            {"id": "c2", "gene": "TP53", "ancestry": "European",
             "expected": "Benign", "predicted": "Benign",
             "match": True, "serious": False},
        ],
    }
    fixture = {
        "cases": [
            {"id": "c1", "gene": "BRCA1", "signals": {"criteria": [
                {"criterion": "PM2", "direction": "pathogenic", "strength": "moderate"}]}},
            {"id": "c2", "gene": "TP53", "signals": {"criteria": [
                {"criterion": "BA1", "direction": "benign", "strength": "stand_alone"}]}},
        ],
    }
    analysis = analyze_failures.analyze(report, fixture, benchmark="tiny_fail_v1")
    analysis["_md_path"] = "/tmp/failure_analysis_tiny_fail_v1.md"
    analysis["_json_path"] = "/tmp/failure_analysis_tiny_fail_v1.json"
    return analysis


class TestReportFailures(unittest.TestCase):
    def test_human_happy_path(self):
        analysis = _tiny_failure_analysis()
        with mock.patch.object(analyze_failures, "run", return_value=analysis):
            rc, out, _ = _run(["report", "failures", "tiny_fail_v1"])
        self.assertEqual(rc, 0)
        self.assertIn("Benchmark: tiny_fail_v1", out)
        self.assertIn("Serious errors:", out)
        self.assertIn("Wrote:", out)

    def test_json_happy_path(self):
        analysis = _tiny_failure_analysis()
        with mock.patch.object(analyze_failures, "run", return_value=analysis):
            rc, out, _ = _run(["report", "failures", "tiny_fail_v1", "--json"])
        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertEqual(payload["totals"]["serious"], 1)

    def test_missing_report_exits_nonzero(self):
        def _boom(name, model_dir=None):
            raise SystemExit("Report not found for benchmark 'ghost'")

        with mock.patch.object(analyze_failures, "run", side_effect=_boom):
            rc, _, err = _run(["report", "failures", "ghost"])
        self.assertEqual(rc, 1)
        self.assertIn("Report not found", err)


if __name__ == "__main__":
    unittest.main()
