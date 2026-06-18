"""Unit tests for ``validation/analytical_validation.py``.

Run from ``ReClass Model/``:

    ../.venv/bin/python -m unittest tests.test_analytical_validation -v
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from validation import analytical_validation as AV


def _crit(name, direction, strength, source="clingen", version="ERepo"):
    return {
        "criterion": name,
        "direction": direction,
        "strength": strength,
        "source": source,
        "version": version,
    }


def _tiny_fixture():
    return {
        "benchmark": "tiny_v1",
        "engine_version": "fixture-1",
        "source_file": "unit-test",
        "note": "tiny analytical-validation fixture",
        "enrichment_summary": {
            "source": "tiny + clingen",
            "provider": "clingen_erepo",
            "clingen_variation_id_matches": 1,
            "match_by_variation_id": 1,
            "match_by_canonical_snv_key": 1,
            "match_by_reference_indel_key": 0,
            "match_by_hgvs_g": 1,
            "matched_total": 3,
            "unmatched": 0,
            "ambiguous": 0,
            "normalization_failed": 0,
            "route_counts": {
                "variation_id": 1,
                "canonical_snv": 1,
                "reference_backed_indel": 0,
                "hgvs_g": 1,
                "unmatched": 0,
            },
        },
        "cases": [
            {
                "id": "P1",
                "gene": "BRCA1",
                "ancestry": "European",
                "population": "European",
                "vcep_group": "Hereditary Cancer VCEP",
                "disease": "Hereditary cancer",
                "variant_class": "SNV",
                "expected": "Pathogenic",
                "signals": {
                    "criteria": [
                        _crit("PVS1", "pathogenic", "very_strong"),
                        _crit("PS1", "pathogenic", "strong"),
                    ]
                },
                "provenance": {"source": "UnitTruth"},
            },
            {
                "id": "B1",
                "gene": "GENE2",
                "ancestry": "European",
                "population": "European",
                "vcep_group": "Benign Controls VCEP",
                "disease": "Benign control disease",
                "variant_class": "SNV",
                "expected": "Benign",
                "signals": {"criteria": [], "gnomad_af": 0.06, "_af_source": "gnomAD_test"},
                "provenance": {"source": "UnitTruth"},
            },
            {
                "id": "P2",
                "gene": "GENE3",
                "ancestry": "Unspecified",
                "population": "South Asian",
                "vcep_group": "Failure VCEP",
                "disease": "Failure disease",
                "variant_class": "missense",
                "expected": "Pathogenic",
                "signals": {"criteria": [], "revel": 0.05},
                "provenance": {"source": "UnitTruth"},
            },
        ],
    }


class TestAnalyticalValidationMetrics(unittest.TestCase):
    def setUp(self):
        self.fixture = _tiny_fixture()
        self.analysis = AV.analyze_benchmark(self.fixture)

    def test_confusion_matrix_reuses_compare_shape(self):
        matrix = self.analysis["confusion_matrix"]
        self.assertEqual(matrix["Pathogenic"]["Pathogenic"], 1)
        self.assertEqual(matrix["Pathogenic"]["Likely Benign"], 1)
        self.assertEqual(matrix["Benign"]["Benign"], 1)

    def test_class_recall_and_provider_coverage(self):
        metrics = self.analysis["metrics"]
        self.assertEqual(metrics["class_recall"]["pathogenic"]["n"], 2)
        self.assertAlmostEqual(metrics["class_recall"]["pathogenic"]["recall"], 0.5)
        self.assertEqual(metrics["class_recall"]["benign"]["n"], 1)
        self.assertAlmostEqual(metrics["class_recall"]["benign"]["recall"], 1.0)

        provider = metrics["provider_coverage"]
        self.assertEqual(provider["clingen"]["present_n"], 1)
        self.assertEqual(provider["gnomad_af"]["present_n"], 1)
        self.assertEqual(provider["revel"]["present_n"], 1)

    def test_source_versions_and_reproducibility(self):
        sources = self.analysis["fixture_source_versions"]
        criteria_versions = {row["name"]: row["count"] for row in sources["criteria_source_versions"]}
        self.assertEqual(criteria_versions["clingen:ERepo"], 2)
        af_sources = {row["name"]: row["count"] for row in sources["allele_frequency_sources"]}
        self.assertEqual(af_sources["gnomAD_test"], 1)
        enrichment = sources["enrichment_source_versions"]
        self.assertEqual(enrichment["match_by_canonical_snv_key"], 1)
        self.assertEqual(enrichment["match_by_hgvs_g"], 1)
        self.assertEqual(enrichment["matched_total"], 3)
        self.assertEqual(enrichment["route_counts"]["canonical_snv"], 1)

        repro = self.analysis["reproducibility_check"]
        self.assertTrue(repro["passed"])
        self.assertEqual(repro["checked_cases"], 3)
        self.assertEqual(repro["mismatch_count"], 0)
        self.assertTrue(repro["sample_hashes"])

    def test_scoped_validation_gates_include_failing_scope(self):
        scoped = self.analysis["scoped_gates"]
        by_gene = {row["scope_value"]: row for row in scoped["gene"]}
        self.assertFalse(by_gene["GENE3"]["gate_pass"])
        self.assertEqual(by_gene["GENE3"]["serious_count"], 1)

        by_population = {row["scope_value"]: row for row in scoped["population"]}
        self.assertTrue(by_population["European"]["gate_pass"])
        self.assertFalse(by_population["South Asian"]["gate_pass"])

    def test_markdown_names_unsigned_clinical_state(self):
        report = AV.build_report(["tiny_v1"], fixtures_dir=self._fixture_dir())
        md = AV.render_markdown(report)
        self.assertIn(AV.CLINICAL_RELEASE_STATE, md)
        self.assertIn("not credentialed clinical sign-off", md)
        self.assertIn("Scoped validation gates", md)
        self.assertIn("FAIL", md)
        self.assertIn("Sensitivity-style recall by class", md)

    def _fixture_dir(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = os.path.join(tmp.name, "tiny_v1.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self.fixture, fh)
        return tmp.name


class TestAnalyticalValidationIO(unittest.TestCase):
    def test_run_writes_markdown_and_json(self):
        with tempfile.TemporaryDirectory() as root:
            fixtures_dir = os.path.join(root, "validation", "fixtures")
            os.makedirs(fixtures_dir)
            with open(os.path.join(fixtures_dir, "tiny_v1.json"), "w", encoding="utf-8") as fh:
                json.dump(_tiny_fixture(), fh)

            report = AV.run(["tiny_v1"], model_dir=root, invocation="python -m validation.analytical_validation")
            self.assertTrue(os.path.exists(report["_md_path"]))
            self.assertTrue(os.path.exists(report["_json_path"]))
            with open(report["_json_path"], encoding="utf-8") as fh:
                payload = json.load(fh)
            self.assertEqual(payload["benchmarks"][0]["benchmark"], "tiny_v1")
            self.assertFalse(payload["clinical_release_signed_off"])


if __name__ == "__main__":
    unittest.main()
