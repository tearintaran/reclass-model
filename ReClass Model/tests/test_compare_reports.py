"""Standard-library unit tests for ``validation/compare_reports.py``.

These tests build tiny in-memory before/after report+fixture pairs so they never
depend on the large real fixtures being present, and they do not require live
evidence-provider calls.
The one test that touches real reports is guarded with ``unittest.skipUnless``
so the shared ``unittest discover`` never breaks for another agent.

Run from the project root (the ``ReClass Model/`` folder)::

    python3 -m unittest tests.test_compare_reports -v
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from validation import compare_reports as CR

MODEL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORTS_DIR = os.path.join(MODEL_DIR, "validation", "reports")
FIXTURES_DIR = os.path.join(MODEL_DIR, "validation", "fixtures")


# --------------------------------------------------------------------------- #
# Builders
# --------------------------------------------------------------------------- #


def _case(cid, gene, group, expected, predicted, match=None, serious=False,
          points=0.0):
    if match is None:
        match = expected == predicted
    return {
        "id": cid,
        "gene": gene,
        "ancestry": group,
        "expected": expected,
        "predicted": predicted,
        "points": points,
        "match": match,
        "serious": serious,
    }


def _report(benchmark, cases, definitive_conc, overall_conc, serious_count,
            serious_rate, gate_pass=False, definitive_n=None):
    n = len(cases)
    return {
        "engine_version": "1.0.0",
        "benchmark": benchmark,
        "run_utc": "2026-01-01T00:00:00+00:00",
        "gate_pass": gate_pass,
        "metrics": {
            "n": n,
            "definitive_n": definitive_n if definitive_n is not None else n,
            "definitive_concordance": definitive_conc,
            "serious_count": serious_count,
            "serious_rate": serious_rate,
            "overall_concordance": overall_conc,
        },
        "cases": cases,
    }


def _fixture_case(cid, expected, criteria=None, revel=None, gnomad_af=None,
                  enrichment=None):
    signals = {"criteria": criteria or []}
    if revel is not None:
        signals["revel"] = revel
    if gnomad_af is not None:
        signals["gnomad_af"] = gnomad_af
    case = {"id": cid, "expected": expected, "signals": signals}
    if enrichment is not None:
        case["enrichment"] = enrichment
    return case


def _crit(name, direction, strength):
    return {"criterion": name, "direction": direction, "strength": strength}


def _before_after_pair():
    """Before: weak evidence. After: enriched, several cases improve."""
    before_cases = [
        # Pathogenic predicted VUS (serious-ish under-call), shared id A1
        _case("A1", "BRCA1", "G1", "Pathogenic", "VUS"),
        # Pathogenic predicted Benign (serious), shared id A2
        _case("A2", "MLH1", "G1", "Pathogenic", "Benign", serious=True),
        # Benign predicted Benign (match), shared id B1
        _case("B1", "AF1", "G2", "Benign", "Benign"),
        # only-before id X1
        _case("X1", "TP53", "G2", "Likely Pathogenic", "VUS"),
    ]
    after_cases = [
        # A1 improves to exact match
        _case("A1", "BRCA1", "G1", "Pathogenic", "Pathogenic"),
        # A2 improves part-way: Benign -> Likely Pathogenic (closer, not exact)
        _case("A2", "MLH1", "G1", "Pathogenic", "Likely Pathogenic"),
        # B1 worsens: Benign -> VUS
        _case("B1", "AF1", "G2", "Benign", "VUS"),
        # only-after id Y1
        _case("Y1", "PKP2", "G2", "Pathogenic", "Pathogenic"),
    ]
    before = _report("before_v1", before_cases, definitive_conc=0.25,
                     overall_conc=0.25, serious_count=1, serious_rate=0.25,
                     definitive_n=4)
    after = _report("after_v1", after_cases, definitive_conc=0.75,
                    overall_conc=0.5, serious_count=0, serious_rate=0.0,
                    definitive_n=4)
    return before, after


# --------------------------------------------------------------------------- #
# Path resolution
# --------------------------------------------------------------------------- #


class TestPathResolution(unittest.TestCase):
    def test_synthetic_special_case(self):
        p = CR.resolve_report_path("synthetic_v1", "/r")
        self.assertTrue(p.endswith("validation_report.json"))

    def test_named_report(self):
        p = CR.resolve_report_path("clinvar_real_v1", "/r")
        self.assertTrue(p.endswith("validation_report_clinvar_real_v1.json"))

    def test_fixture_path(self):
        p = CR.resolve_fixture_path("clinvar_enriched_v1", "/f")
        self.assertTrue(p.endswith("clinvar_enriched_v1.json"))

    def test_harness_hint_mentions_benchmark(self):
        self.assertIn("clinvar_enriched_v1", CR.harness_hint("clinvar_enriched_v1"))
        self.assertIn("harness.py", CR.harness_hint("synthetic_v1"))


# --------------------------------------------------------------------------- #
# Metric deltas
# --------------------------------------------------------------------------- #


class TestMetricDeltas(unittest.TestCase):
    def setUp(self):
        self.before, self.after = _before_after_pair()
        self.m = CR.metric_deltas(self.before["metrics"], self.after["metrics"])

    def test_case_count_delta(self):
        self.assertEqual(self.m["case_count"]["before"], 4)
        self.assertEqual(self.m["case_count"]["after"], 4)
        self.assertEqual(self.m["case_count"]["delta"], 0)

    def test_definitive_concordance_delta(self):
        block = self.m["definitive_concordance"]
        self.assertAlmostEqual(block["delta"], 0.5)

    def test_serious_count_delta(self):
        self.assertEqual(self.m["serious_count"]["delta"], -1)

    def test_overall_concordance_delta(self):
        self.assertAlmostEqual(self.m["overall_concordance"]["delta"], 0.25)

    def test_missing_metric_yields_none_delta(self):
        m = CR.metric_deltas({"n": 5}, {})
        self.assertEqual(m["case_count"]["before"], 5)
        self.assertIsNone(m["case_count"]["delta"])
        self.assertIsNone(m["definitive_concordance"]["delta"])


# --------------------------------------------------------------------------- #
# Per-tier recall
# --------------------------------------------------------------------------- #


class TestPerTierRecall(unittest.TestCase):
    def test_basic_recall(self):
        cases = [
            _case("1", "g", "G", "Pathogenic", "Pathogenic"),
            _case("2", "g", "G", "Pathogenic", "VUS"),
            _case("3", "g", "G", "Benign", "Benign"),
        ]
        rec = CR.per_tier_recall(cases)
        self.assertEqual(rec["Pathogenic"]["n"], 2)
        self.assertEqual(rec["Pathogenic"]["matched"], 1)
        self.assertAlmostEqual(rec["Pathogenic"]["recall"], 0.5)
        self.assertAlmostEqual(rec["Benign"]["recall"], 1.0)
        # absent tier zero-filled
        self.assertEqual(rec["VUS"]["n"], 0)
        self.assertEqual(rec["VUS"]["recall"], 0.0)

    def test_recall_delta(self):
        before, after = _before_after_pair()
        d = CR.per_tier_recall_delta(before["cases"], after["cases"])
        # Pathogenic: before A1,A2 both wrong (0/2); after A1 right, Y1 right -> 2/3
        self.assertAlmostEqual(d["Pathogenic"]["before"]["recall"], 0.0)
        self.assertAlmostEqual(d["Pathogenic"]["after"]["recall"], 2.0 / 3.0)
        self.assertGreater(d["Pathogenic"]["recall_delta"], 0)
        # Benign: before B1 right (1/1); after B1 wrong (0/1)
        self.assertAlmostEqual(d["Benign"]["recall_delta"], -1.0)

    def test_all_tiers_present(self):
        d = CR.per_tier_recall_delta([], [])
        for tier in CR.TIERS:
            self.assertIn(tier, d)


# --------------------------------------------------------------------------- #
# Confusion delta
# --------------------------------------------------------------------------- #


class TestConfusionDelta(unittest.TestCase):
    def test_confusion_matrix_counts(self):
        cases = [
            _case("1", "g", "G", "Pathogenic", "VUS"),
            _case("2", "g", "G", "Pathogenic", "VUS"),
        ]
        cm = CR.confusion_matrix(cases)
        self.assertEqual(cm["Pathogenic"]["VUS"], 2)
        self.assertEqual(cm["Pathogenic"]["Pathogenic"], 0)

    def test_delta_only_nonzero(self):
        before, after = _before_after_pair()
        cd = CR.confusion_delta(before["cases"], after["cases"])
        # before A1 Pathogenic->VUS, after A1 Pathogenic->Pathogenic
        self.assertEqual(cd["Pathogenic"]["VUS"], -1)
        self.assertEqual(cd["Pathogenic"]["Pathogenic"], 2)  # A1 + Y1 new
        # cells with zero delta are omitted
        for row in cd.values():
            for d in row.values():
                self.assertNotEqual(d, 0)


# --------------------------------------------------------------------------- #
# Overlap
# --------------------------------------------------------------------------- #


class TestOverlap(unittest.TestCase):
    def setUp(self):
        self.before, self.after = _before_after_pair()
        self.o = CR.overlap_changes(self.before["cases"], self.after["cases"])

    def test_overlap_counts(self):
        self.assertEqual(self.o["before_n"], 4)
        self.assertEqual(self.o["after_n"], 4)
        self.assertEqual(self.o["overlap_n"], 3)  # A1, A2, B1
        self.assertEqual(self.o["only_before_n"], 1)  # X1
        self.assertEqual(self.o["only_after_n"], 1)   # Y1

    def test_improved_worsened_unchanged(self):
        # A1: VUS->Pathogenic (improved), A2: Benign->LP (improved),
        # B1: Benign->VUS (worsened)
        self.assertEqual(self.o["improved"], 2)
        self.assertEqual(self.o["worsened"], 1)
        self.assertEqual(self.o["unchanged"], 0)
        self.assertIn("A1", self.o["improved_ids"])
        self.assertIn("A2", self.o["improved_ids"])
        self.assertIn("B1", self.o["worsened_ids"])

    def test_match_flips(self):
        # A1 became exact match; B1 lost exact match; A2 still not exact match
        self.assertEqual(self.o["became_match"], 1)
        self.assertEqual(self.o["lost_match"], 1)
        self.assertIn("A1", self.o["became_match_ids"])
        self.assertIn("B1", self.o["lost_match_ids"])

    def test_unchanged_distance(self):
        before = [_case("1", "g", "G", "VUS", "Benign")]
        after = [_case("1", "g", "G", "VUS", "Pathogenic")]
        o = CR.overlap_changes(before, after)
        # Benign and Pathogenic are both two ranks away from VUS -> unchanged
        self.assertEqual(o["unchanged"], 1)
        self.assertEqual(o["improved"], 0)
        self.assertEqual(o["worsened"], 0)


# --------------------------------------------------------------------------- #
# Evidence coverage
# --------------------------------------------------------------------------- #


class TestEvidenceCoverage(unittest.TestCase):
    def test_coverage_counts(self):
        fixture = {
            "cases": [
                _fixture_case("1", "Pathogenic",
                              criteria=[_crit("PVS1", "pathogenic", "very_strong")],
                              revel=0.9, gnomad_af=0.0001,
                              enrichment={"clingen_variation_id_match": True}),
                _fixture_case("2", "Benign", gnomad_af=0.2),
                _fixture_case("3", "VUS"),
            ]
        }
        cov = CR.evidence_coverage(fixture)
        self.assertEqual(cov["cases"], 3)
        self.assertEqual(cov["with_criteria"], 1)
        self.assertEqual(cov["with_revel"], 1)
        self.assertEqual(cov["with_gnomad_af"], 2)
        self.assertEqual(cov["with_enrichment"], 1)
        self.assertEqual(cov["criteria_buckets"]["0"], 2)
        self.assertEqual(cov["criteria_buckets"]["1-2"], 1)

    def test_coverage_none_for_missing_fixture(self):
        self.assertIsNone(CR.evidence_coverage(None))

    def test_coverage_delta(self):
        before_fx = {
            "cases": [
                _fixture_case("1", "Pathogenic"),
                _fixture_case("2", "Pathogenic"),
            ]
        }
        after_fx = {
            "cases": [
                _fixture_case("1", "Pathogenic",
                              criteria=[_crit("PVS1", "pathogenic", "very_strong")],
                              enrichment={"clingen_variation_id_match": True}),
                _fixture_case("2", "Pathogenic"),
            ]
        }
        d = CR.evidence_coverage_delta(before_fx, after_fx)
        self.assertEqual(d["delta"]["with_criteria"]["delta"], 1)
        self.assertEqual(d["delta"]["with_enrichment"]["delta"], 1)
        self.assertEqual(d["delta"]["criteria_buckets"]["1-2"]["delta"], 1)

    def test_coverage_delta_none_when_no_fixtures(self):
        self.assertIsNone(CR.evidence_coverage_delta(None, None))

    def test_coverage_delta_one_sided(self):
        after_fx = {"cases": [_fixture_case("1", "Pathogenic")]}
        d = CR.evidence_coverage_delta(None, after_fx)
        self.assertIsNotNone(d)
        self.assertIsNone(d["before"])
        self.assertEqual(d["after"]["cases"], 1)


# --------------------------------------------------------------------------- #
# Top-level compare + rendering
# --------------------------------------------------------------------------- #


class TestCompareAndRender(unittest.TestCase):
    def setUp(self):
        self.before, self.after = _before_after_pair()

    def test_compare_structure(self):
        comp = CR.compare(self.before, self.after,
                          before_name="before_v1", after_name="after_v1")
        self.assertEqual(comp["before"], "before_v1")
        self.assertEqual(comp["after"], "after_v1")
        self.assertIn("metrics", comp)
        self.assertIn("per_tier_recall", comp)
        self.assertIn("confusion_delta", comp)
        self.assertIn("overlap", comp)
        # no fixtures supplied -> no evidence_coverage key
        self.assertNotIn("evidence_coverage", comp)

    def test_compare_with_fixtures(self):
        before_fx = {"cases": [_fixture_case("A1", "Pathogenic")]}
        after_fx = {"cases": [
            _fixture_case("A1", "Pathogenic",
                          criteria=[_crit("PVS1", "pathogenic", "very_strong")])]}
        comp = CR.compare(self.before, self.after,
                          before_fixture=before_fx, after_fixture=after_fx)
        self.assertIn("evidence_coverage", comp)
        self.assertEqual(
            comp["evidence_coverage"]["delta"]["with_criteria"]["delta"], 1)

    def test_markdown_renders(self):
        comp = CR.compare(self.before, self.after,
                          before_name="before_v1", after_name="after_v1")
        md = CR.render_markdown(comp)
        self.assertIn("Validation comparison", md)
        self.assertIn("before_v1", md)
        self.assertIn("after_v1", md)
        self.assertIn("Headline metric deltas", md)
        self.assertIn("Per-tier recall", md)
        self.assertIn("Matched-case overlap", md)
        self.assertIn("Confusion-matrix deltas", md)

    def test_markdown_includes_coverage_when_present(self):
        before_fx = {"cases": [_fixture_case("A1", "Pathogenic")]}
        after_fx = {"cases": [_fixture_case("A1", "Pathogenic",
                              criteria=[_crit("PVS1", "pathogenic", "very_strong")])]}
        comp = CR.compare(self.before, self.after,
                          before_fixture=before_fx, after_fixture=after_fx)
        md = CR.render_markdown(comp)
        self.assertIn("Evidence coverage", md)

    def test_stdout_summary(self):
        comp = CR.compare(self.before, self.after,
                          before_name="before_v1", after_name="after_v1")
        s = CR.render_stdout_summary(comp)
        self.assertIn("before_v1 -> after_v1", s)
        self.assertIn("Definitive concordance", s)


# --------------------------------------------------------------------------- #
# run() IO + missing-report error
# --------------------------------------------------------------------------- #


class TestRunIO(unittest.TestCase):
    def _write_model_dir(self, d, reports=None, fixtures=None):
        rdir = os.path.join(d, "validation", "reports")
        fdir = os.path.join(d, "validation", "fixtures")
        os.makedirs(rdir, exist_ok=True)
        os.makedirs(fdir, exist_ok=True)
        for name, payload in (reports or {}).items():
            path = CR.resolve_report_path(name, rdir)
            with open(path, "w") as fh:
                json.dump(payload, fh)
        for name, payload in (fixtures or {}).items():
            with open(os.path.join(fdir, name + ".json"), "w") as fh:
                json.dump(payload, fh)

    def test_run_writes_outputs_and_does_not_touch_inputs(self):
        before, after = _before_after_pair()
        with tempfile.TemporaryDirectory() as d:
            self._write_model_dir(
                d, reports={"before_v1": before, "after_v1": after})
            rdir = os.path.join(d, "validation", "reports")
            before_path = CR.resolve_report_path("before_v1", rdir)
            with open(before_path) as fh:
                before_before = fh.read()

            comp = CR.run("before_v1", "after_v1", model_dir=d)
            self.assertTrue(os.path.exists(comp["_md_path"]))
            self.assertTrue(os.path.exists(comp["_json_path"]))
            self.assertTrue(comp["_md_path"].endswith(
                "comparison_before_v1_vs_after_v1.md"))

            # baseline report untouched
            with open(before_path) as fh:
                self.assertEqual(fh.read(), before_before)

            with open(comp["_json_path"]) as fh:
                reloaded = json.load(fh)
            self.assertEqual(reloaded["overlap"]["overlap_n"], 3)
            self.assertIn("generated_utc", reloaded)

    def test_run_uses_fixtures_when_present(self):
        before, after = _before_after_pair()
        before_fx = {"cases": [_fixture_case("A1", "Pathogenic")]}
        after_fx = {"cases": [_fixture_case("A1", "Pathogenic",
                              criteria=[_crit("PVS1", "pathogenic", "very_strong")])]}
        with tempfile.TemporaryDirectory() as d:
            self._write_model_dir(
                d,
                reports={"before_v1": before, "after_v1": after},
                fixtures={"before_v1": before_fx, "after_v1": after_fx})
            comp = CR.run("before_v1", "after_v1", model_dir=d)
            self.assertIn("evidence_coverage", comp)

    def test_missing_report_raises_with_hint(self):
        before, _ = _before_after_pair()
        with tempfile.TemporaryDirectory() as d:
            self._write_model_dir(d, reports={"before_v1": before})
            with self.assertRaises(SystemExit) as ctx:
                CR.run("before_v1", "missing_v1", model_dir=d)
            msg = str(ctx.exception)
            self.assertIn("missing_v1", msg)
            self.assertIn("harness.py", msg)


# --------------------------------------------------------------------------- #
# Optional smoke check against real reports
# --------------------------------------------------------------------------- #


class TestRealReportsIfPresent(unittest.TestCase):
    SYNTH = os.path.join(REPORTS_DIR, "validation_report.json")
    CLINGEN = os.path.join(REPORTS_DIR, "validation_report_clingen_real_v1.json")

    @unittest.skipUnless(
        os.path.exists(SYNTH) and os.path.exists(CLINGEN),
        "synthetic_v1 / clingen_real_v1 reports not present")
    def test_real_comparison_runs(self):
        synth = CR._load_json(self.SYNTH)
        clingen = CR._load_json(self.CLINGEN)
        comp = CR.compare(synth, clingen,
                          before_name="synthetic_v1",
                          after_name="clingen_real_v1")
        md = CR.render_markdown(comp)
        self.assertIn("synthetic_v1", md)
        self.assertIn("clingen_real_v1", md)
        self.assertIsInstance(comp["overlap"]["overlap_n"], int)


if __name__ == "__main__":
    unittest.main()
