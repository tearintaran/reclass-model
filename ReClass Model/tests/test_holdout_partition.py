"""Tests for the blinded, pre-registered held-out evaluation machinery.

Covers the deterministic case-level holdout partition
(``validation/fixture_splits.py``), the anti-leakage wiring in
``validation/calibration.py``, the pre-registration contract
(``validation/preregistration.json``), and the held-out evaluator
(``validation/holdout_eval.py``).

The heavy checks that re-derive the pinned partition fingerprints from the real
multi-megabyte fixtures are skipped automatically when those fixtures are absent,
so the suite still runs in a minimal checkout.

Run from ``ReClass Model/``::

    ../.venv/bin/python -m unittest tests.test_holdout_partition -v
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from validation import fixture_splits as FS  # noqa: E402
from validation import holdout_eval as HE  # noqa: E402
from engine import config as C  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURES_DIR = os.path.join(os.path.dirname(HERE), "validation", "fixtures")
PREREG_PATH = os.path.join(os.path.dirname(HERE), "validation", "preregistration.json")


def _locus_case(cid, chrom, pos, ref, alt, expected="VUS"):
    return {
        "id": cid,
        "expected": expected,
        "locus": {"chrom": chrom, "pos": pos, "ref": ref, "alt": alt},
        "signals": {"criteria": []},
    }


def _load_prereg():
    with open(PREREG_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def _fixture_path(name):
    return os.path.join(FIXTURES_DIR, name + ".json")


class PartitionRuleTests(unittest.TestCase):
    def test_identity_prefers_locus_then_id(self):
        c = _locus_case("CV-1", "1", 100, "A", "G")
        self.assertEqual(FS.case_identity_key(c), "GRCH38-1-100-A-G")
        # Incomplete locus falls back to the stable id.
        c2 = {"id": "CV-2", "locus": {"chrom": "1", "pos": None, "ref": "A", "alt": "G"}}
        self.assertEqual(FS.case_identity_key(c2), "ID:CV-2")
        # No usable identity is an error, not a silent bucket.
        with self.assertRaises(ValueError):
            FS.case_identity_key({"locus": {}})

    def test_partition_is_deterministic(self):
        c = _locus_case("CV-1", "7", 55249071, "C", "T")
        first = FS.case_partition(c)
        for _ in range(5):
            self.assertEqual(FS.case_partition(c), first)

    def test_partition_is_label_blind(self):
        # Same locus, every possible label -> identical assignment.
        base = _locus_case("CV-9", "2", 47803500, "G", "A")
        assignments = set()
        for label in ("Pathogenic", "Likely Pathogenic", "VUS", "Likely Benign", "Benign"):
            c = dict(base)
            c["expected"] = label
            assignments.add(FS.case_partition(c))
        self.assertEqual(len(assignments), 1)

    def test_partition_depends_only_on_identity_not_surrounding_data(self):
        # Cross-fixture consistency: same locus, different ids/genes/signals -> same split.
        a = _locus_case("CG-100", "13", 32316461, "T", "C", expected="Pathogenic")
        a["gene"] = "BRCA2"
        b = _locus_case("CV-999", "13", 32316461, "T", "C", expected="Benign")
        b["gene"] = "SOMETHING_ELSE"
        b["signals"] = {"criteria": [], "revel": 0.9}
        self.assertEqual(FS.case_partition(a), FS.case_partition(b))

    def test_disjoint_and_complete(self):
        cases = [_locus_case(f"V-{i}", "1", 1000 + i, "A", "G") for i in range(500)]
        parts = FS.partition_cases(cases)
        dev = {c["id"] for c in parts[FS.DEVELOPMENT]}
        hold = {c["id"] for c in parts[FS.HOLDOUT]}
        self.assertEqual(dev & hold, set())
        self.assertEqual(len(dev) + len(hold), len(cases))
        self.assertEqual(
            {c["id"] for c in FS.development_cases(cases)}, dev
        )
        self.assertEqual({c["id"] for c in FS.holdout_cases(cases)}, hold)

    def test_fraction_within_tolerance(self):
        # ~30% holdout over a large blind sample of distinct loci.
        cases = [_locus_case(f"V-{i}", str((i % 22) + 1), 10_000 + i, "A", "C")
                 for i in range(8000)]
        frac = len(FS.holdout_cases(cases)) / len(cases)
        self.assertAlmostEqual(frac, FS.HOLDOUT_PARTITION_FRACTION, delta=0.02)

    def test_fingerprint_is_stable_and_order_independent(self):
        cases = [_locus_case(f"V-{i}", "5", 2000 + i, "T", "A") for i in range(300)]
        fp1 = FS.partition_fingerprint(cases)
        fp2 = FS.partition_fingerprint(list(reversed(cases)))
        self.assertEqual(fp1, fp2)
        self.assertEqual(fp1["n_holdout"], len(FS.holdout_cases(cases)))


class PreregistrationContractTests(unittest.TestCase):
    def test_locked_config_hash_matches_live_engine(self):
        # If the engine config is retuned without re-registering, this fails loudly.
        prereg = _load_prereg()
        live = C.config_fingerprint()
        self.assertEqual(prereg["locked_engine"]["config_hash"], live["config_hash"])
        self.assertEqual(prereg["locked_engine"]["engine_version"], live["engine_version"])

    def test_split_rule_constants_match_code(self):
        prereg = _load_prereg()
        rule = prereg["split_rule"]
        self.assertEqual(rule["salt"], FS.HOLDOUT_PARTITION_SALT)
        self.assertEqual(rule["holdout_fraction"], FS.HOLDOUT_PARTITION_FRACTION)

    def test_registered_benchmarks_are_the_validation_split(self):
        prereg = _load_prereg()
        self.assertEqual(
            set(prereg["expected_partition"]), FS.partitioned_benchmark_names()
        )

    def test_pinned_fingerprints_reproduce_from_real_fixtures(self):
        prereg = _load_prereg()
        for name, expected in prereg["expected_partition"].items():
            path = _fixture_path(name)
            if not os.path.exists(path):
                self.skipTest(f"real fixture {name} not present")
            with open(path, encoding="utf-8") as fh:
                cases = json.load(fh)["cases"]
            fp = FS.partition_fingerprint(cases)
            self.assertEqual(
                fp["sha256"], expected["holdout_sha256"],
                f"{name} holdout fingerprint drifted from the registration",
            )
            self.assertEqual(fp["n_holdout"], expected["n_holdout"])


class CalibrationExclusionTests(unittest.TestCase):
    def test_calibration_loader_drops_holdout_cases(self):
        from validation import calibration as CB

        name = "clingen_real_v1"
        if not os.path.exists(_fixture_path(name)):
            self.skipTest("real fixture not present")
        bench = CB.load_benchmark(name)
        self.assertTrue(bench.get("_development_only"))
        self.assertGreater(bench.get("_holdout_excluded", 0), 0)
        # No loaded case may belong to the holdout sub-split.
        leaked = [c for c in bench["cases"] if FS.case_partition(c) == FS.HOLDOUT]
        self.assertEqual(leaked, [])
        # Calibration surfaces the exclusion in its analysis payload.
        analysis = CB.calibrate(bench, run_sensitivity=False)
        self.assertTrue(analysis["development_only"])
        self.assertEqual(analysis["holdout_excluded"], bench["_holdout_excluded"])


class WilsonIntervalTests(unittest.TestCase):
    def test_point_estimate_brackets_interval(self):
        lo, hi = HE.wilson_interval(95, 100)
        self.assertLess(lo, 0.95)
        self.assertGreater(hi, 0.95)
        self.assertGreaterEqual(lo, 0.0)
        self.assertLessEqual(hi, 1.0)

    def test_empty_is_degenerate(self):
        self.assertEqual(HE.wilson_interval(0, 0), (0.0, 0.0))

    def test_perfect_proportion_upper_is_one(self):
        lo, hi = HE.wilson_interval(50, 50)
        self.assertLessEqual(hi, 1.0)
        self.assertLess(lo, 1.0)


class AssessmentLogicTests(unittest.TestCase):
    """assess() applies the frozen thresholds correctly on synthetic inputs."""

    def _prereg(self):
        return _load_prereg()

    def _bench(self, def_conc, ser_rate, def_ci_lo, ser_ci_hi, n=1000, def_n=800,
               dev_def=None):
        dev_def = def_conc if dev_def is None else dev_def
        block = {
            "holdout": {
                "n": n, "definitive_n": def_n,
                "definitive_concordance": def_conc,
                "definitive_concordance_ci95": [def_ci_lo, 1.0],
                "serious_count": int(round(ser_rate * n)),
                "serious_rate": ser_rate,
                "serious_rate_ci95": [0.0, ser_ci_hi],
            },
            "development": {"definitive_concordance": dev_def},
            "overfit_gap": dev_def - def_conc,
        }
        return block

    def test_h1_pass_and_contrast(self):
        prereg = self._prereg()
        by = {
            "clingen_real_v1": self._bench(0.95, 0.001, 0.94, 0.003),
            "clinvar_real_v1": self._bench(0.05, 0.002, 0.045, 0.006),
            "clinvar_enriched_v1": self._bench(0.44, 0.0008, 0.43, 0.002),
        }
        a = HE.assess(prereg, by)
        self.assertTrue(a["H1"]["pass"])
        self.assertTrue(a["verdict_pass"])
        self.assertTrue(a["H3_contrast"]["pass"])  # 0.44 - 0.05 = 0.39 >= 0.15
        self.assertFalse(any(v["flagged"] for v in a["overfit"].values()))

    def test_h1_fails_when_lower_ci_below_bar(self):
        prereg = self._prereg()
        by = {
            "clingen_real_v1": self._bench(0.86, 0.001, 0.83, 0.003),  # lower CI < 0.85
            "clinvar_real_v1": self._bench(0.05, 0.002, 0.045, 0.006),
            "clinvar_enriched_v1": self._bench(0.44, 0.0008, 0.43, 0.002),
        }
        a = HE.assess(prereg, by)
        self.assertFalse(a["H1"]["definitive_pass"])
        self.assertFalse(a["verdict_pass"])

    def test_h1_fails_when_serious_upper_ci_at_or_above_bar(self):
        prereg = self._prereg()
        by = {
            "clingen_real_v1": self._bench(0.95, 0.008, 0.94, 0.012),  # serious upper > 0.01
            "clinvar_real_v1": self._bench(0.05, 0.002, 0.045, 0.006),
            "clinvar_enriched_v1": self._bench(0.44, 0.0008, 0.43, 0.002),
        }
        a = HE.assess(prereg, by)
        self.assertFalse(a["H1"]["serious_pass"])

    def test_overfit_flagged_when_holdout_trails_dev(self):
        prereg = self._prereg()
        by = {
            "clingen_real_v1": self._bench(0.88, 0.001, 0.86, 0.003, dev_def=0.95),  # gap 7 pp
            "clinvar_real_v1": self._bench(0.05, 0.002, 0.045, 0.006),
            "clinvar_enriched_v1": self._bench(0.44, 0.0008, 0.43, 0.002),
        }
        a = HE.assess(prereg, by)
        self.assertTrue(a["overfit"]["clingen_real_v1"]["flagged"])


if __name__ == "__main__":
    unittest.main()
