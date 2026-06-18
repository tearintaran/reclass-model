"""Standard-library unit tests for ``validation/calibration.py`` (Part B, tasks 5-6).

Builds a tiny in-memory benchmark so the per-VCEP/gene calibration, low-performing
triage, threshold-sensitivity sweep, and serious-discordance review are exercised
without the large real fixtures. All pure/offline.

Run from the project root (the ``ReClass Model/`` folder)::

    ../.venv/bin/python -m unittest tests.test_calibration -v
"""

import os
import json
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from validation import calibration as CB
from validation import fixture_splits as FS
from validation import harness as H
from engine.config_registry import get_config


def _p(criterion, strength):
    return {"criterion": criterion, "direction": "pathogenic", "strength": strength}


def _case(cid, gene, ancestry, expected, signals):
    return {"id": cid, "gene": gene, "ancestry": ancestry,
            "expected": expected, "signals": signals}


# A controlled benchmark with one VCEP group and two serious discordances.
BENCHMARK = {
    "benchmark": "calib_tiny_v1",
    "cases": [
        # Pathogenic reproduced (PVS1+PS1 = 12 -> Pathogenic)
        _case("C1", "BRCA1", "Cardiomyopathy VCEP", "Pathogenic",
              {"criteria": [_p("PVS1", "very_strong"), _p("PS1", "strong")]}),
        # Benign expected, but PVS1 alone (8) -> Likely Pathogenic => SERIOUS
        _case("C2", "BRCA1", "Cardiomyopathy VCEP", "Benign",
              {"criteria": [_p("PVS1", "very_strong")]}),
        # Benign reproduced via BA1 frequency
        _case("C3", "MLH1", "Cardiomyopathy VCEP", "Benign",
              {"gnomad_af": 0.2}),
        # Pathogenic expected, but common AF -> BA1 -> Benign => SERIOUS
        _case("C4", "TP53", "European", "Pathogenic",
              {"gnomad_af": 0.2}),
    ],
}


class TestPureMetrics(unittest.TestCase):
    def setUp(self):
        self.results = H.evaluate(BENCHMARK)

    def test_group_metrics_by_vcep(self):
        blocks = CB.group_metrics(self.results, "ancestry", kind_field="group_kind")
        by = {b["group"]: b for b in blocks}
        cardio = by["Cardiomyopathy VCEP"]
        self.assertEqual(cardio["kind"], "panel")
        self.assertEqual(cardio["n"], 3)
        self.assertEqual(cardio["serious"], 1)  # C2

    def test_group_metrics_sorted_worst_first(self):
        blocks = CB.group_metrics(self.results, "gene")
        # Genes with serious discordance sort ahead of clean ones.
        self.assertGreater(blocks[0]["serious"], 0)

    def test_low_performing_flags_serious_regardless_of_size(self):
        blocks = CB.group_metrics(self.results, "gene")
        flagged = CB.low_performing_groups(blocks, min_n=999)  # huge -> size never trips
        genes = {b["group"] for b in flagged}
        # BRCA1 and TP53 each carry a serious discordance.
        self.assertIn("BRCA1", genes)
        self.assertIn("TP53", genes)
        self.assertNotIn("MLH1", genes)
        for b in flagged:
            self.assertTrue(b["reasons"])

    def test_serious_discordances_directions(self):
        sd = CB.serious_discordances(self.results)
        by = {d["id"]: d for d in sd}
        self.assertEqual(set(by), {"C2", "C4"})
        self.assertEqual(by["C2"]["direction"], "benign_called_pathogenic")
        self.assertEqual(by["C4"]["direction"], "pathogenic_called_benign")


class TestThresholdSensitivity(unittest.TestCase):
    def test_holdout_fixture_is_rejected_for_calibration_and_thresholds(self):
        holdout = dict(BENCHMARK)
        holdout["benchmark"] = "holdout_unit_v1"
        holdout["fixture_split"] = "holdout"
        with self.assertRaises(FS.HoldoutFixtureError):
            CB.calibrate(holdout, run_sensitivity=False)
        with self.assertRaises(FS.HoldoutFixtureError):
            CB.threshold_sensitivity(holdout)

    def test_sensitivity_reports_deltas_vs_base(self):
        ts = CB.threshold_sensitivity(BENCHMARK)
        self.assertIn("base", ts)
        self.assertTrue(ts["perturbations"])
        names = {r["name"] for r in ts["perturbations"]}
        self.assertIn("pathogenic_cutoffs_+2", names)
        for r in ts["perturbations"]:
            # Each perturbation carries a fingerprinted (non-base) engine version.
            self.assertNotEqual(r["engine_version"], get_config().engine_version)
            self.assertIn("definitive_concordance_delta", r)

    def test_score_under_config_matches_base_harness(self):
        # Scoring under config=None equals the base harness metrics.
        m = CB.score_under_config(BENCHMARK, None)
        hm = H.compute_metrics(H.evaluate(BENCHMARK))
        self.assertAlmostEqual(m["definitive_concordance"], hm["definitive_concordance"])
        self.assertEqual(m["serious_count"], hm["serious_count"])

    def test_default_perturbations_are_single_knob(self):
        perts = CB.default_perturbations()
        self.assertEqual(len(perts), 5)
        for name, desc, cfg in perts:
            self.assertTrue(desc)
            self.assertFalse(cfg.is_base)


class TestCalibrateAndRender(unittest.TestCase):
    def test_split_manifests_are_disjoint_from_holdout(self):
        FS.assert_split_manifests_disjoint()
        members = FS.split_members()
        self.assertFalse(members[FS.DEVELOPMENT] & members[FS.HOLDOUT])
        self.assertFalse(members[FS.VALIDATION] & members[FS.HOLDOUT])

    def test_calibrate_structure(self):
        a = CB.calibrate(BENCHMARK)
        for key in ("overall", "by_vcep", "by_gene", "low_performing_genes",
                    "serious_discordances", "review_packets", "threshold_sensitivity",
                    "config_fingerprint"):
            self.assertIn(key, a)
        self.assertEqual(a["overall"]["serious"], 2)
        self.assertEqual(len(a["serious_discordances"]), 2)
        self.assertEqual(len(a["review_packets"]), 2)

    def test_calibration_review_packet_round_trips_json(self):
        benchmark = {
            "benchmark": "packet_tiny_v1",
            "cases": [
                _case(
                    "PKT1",
                    "GENE",
                    "European",
                    "Pathogenic",
                    {
                        "gnomad_af": 0.2,
                        "ps4_cohort_counts": {"affected": 6, "unaffected": 0},
                        "mane_select_transcript": "NM_000000.1",
                    },
                )
            ],
        }
        packet = CB.calibrate(benchmark, run_sensitivity=False)["review_packets"][0]
        self.assertIn("reviewer_decision", packet)
        self.assertIn("override_proposal", packet)
        self.assertIn("sign_off", packet)
        self.assertIn("accepted", packet["override_proposal"])
        self.assertIn("rejected", packet["override_proposal"])
        self.assertEqual(packet["evidence_summary"]["ps4_cohort_counts"]["affected"], 6)
        self.assertEqual(packet["evidence_summary"]["mane_transcript"], "NM_000000.1")
        self.assertEqual(json.loads(json.dumps(packet)), packet)

    def test_markdown_has_all_sections(self):
        md = CB.render_markdown(CB.calibrate(BENCHMARK))
        for heading in ("Calibration by VCEP / panel group", "Calibration by gene",
                        "Low-performing groups (triage)",
                        "Threshold-sensitivity analysis",
                        "Serious pathogenic<->benign discordances"):
            self.assertIn(heading, md)

    def test_run_writes_outputs(self):
        with tempfile.TemporaryDirectory() as d:
            fdir = os.path.join(d, "validation", "fixtures")
            rdir = os.path.join(d, "validation", "reports")
            os.makedirs(fdir)
            os.makedirs(rdir)
            with open(os.path.join(fdir, "calib_tiny_v1.json"), "w") as fh:
                json.dump(BENCHMARK, fh)
            # Point the module's dirs at the temp tree for this run.
            old_fix, old_rep = CB.FIXTURES_DIR, CB.REPORTS_DIR
            CB.FIXTURES_DIR, CB.REPORTS_DIR = fdir, rdir
            try:
                a = CB.run("calib_tiny_v1")
            finally:
                CB.FIXTURES_DIR, CB.REPORTS_DIR = old_fix, old_rep
            self.assertTrue(os.path.exists(a["_md_path"]))
            self.assertTrue(os.path.exists(a["_json_path"]))


if __name__ == "__main__":
    unittest.main()
