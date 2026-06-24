"""Unit tests for the evidence-coverage model and roll-ups (job1 task 2).

Pure, offline: no database, no network. Exercises the blocked-case logic and the
by-dimension breakdowns that back the coverage dashboard.
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from evidence import coverage as cov  # noqa: E402


class TestCategoryMapping(unittest.TestCase):
    def test_known_criteria_map_to_categories(self) -> None:
        self.assertEqual(cov.category_of("PVS1"), "lof")
        self.assertEqual(cov.category_of("PS3"), "functional")
        self.assertEqual(cov.category_of("BS3"), "functional")
        self.assertEqual(cov.category_of("PS4"), "case_control")
        self.assertEqual(cov.category_of("BA1"), "frequency")

    def test_unknown_criterion_is_uncategorized(self) -> None:
        self.assertIsNone(cov.category_of("ZZ9"))

    def test_present_categories_dedupes(self) -> None:
        self.assertEqual(
            cov.present_categories(["PS3", "BS3", "PP1"]),
            {"functional", "segregation"},
        )


class TestComputeCoverage(unittest.TestCase):
    def test_lof_variant_missing_functional_is_blocked(self) -> None:
        rec = cov.compute_coverage(
            "GRCh38-1-100-A-G", ["PVS1"], variant_class="lof", gene="BRCA1",
        )
        self.assertIn("functional", rec.missing_categories)
        self.assertNotIn("lof", rec.missing_categories)  # PVS1 present
        self.assertTrue(rec.blocked)
        self.assertIn("functional", rec.blocking_reason or "")

    def test_complete_blocking_evidence_not_blocked(self) -> None:
        # Provide one criterion from every blocking category expected for lof.
        rec = cov.compute_coverage(
            "GRCh38-1-100-A-G",
            ["PVS1", "PS3", "PM3", "PP1", "PS4"],
            variant_class="lof",
        )
        self.assertFalse(rec.blocked)
        self.assertIsNone(rec.blocking_reason)

    def test_unknown_class_uses_default_expectation(self) -> None:
        rec = cov.compute_coverage("GRCh38-1-100-A-G", [], variant_class="weird")
        self.assertTrue(rec.blocked)
        # default expects functional/segregation/case_control/phasing among others
        self.assertIn("functional", rec.missing_categories)


class TestRollup(unittest.TestCase):
    def _records(self):
        return [
            cov.compute_coverage("GRCh38-1-1-A-G", ["PVS1"], variant_class="lof",
                                 gene="BRCA1", provider="clingen"),
            cov.compute_coverage("GRCh38-1-2-A-G", ["PVS1", "PS3", "PM3", "PP1", "PS4"],
                                 variant_class="lof", gene="BRCA1", provider="clingen"),
            cov.compute_coverage("GRCh38-2-3-A-G", [], variant_class="missense",
                                 gene="TP53", provider="revel"),
        ]

    def test_rollup_by_gene_counts_blocked(self) -> None:
        out = cov.rollup(self._records(), "gene")
        self.assertEqual(out["BRCA1"]["total"], 2)
        self.assertEqual(out["BRCA1"]["blocked"], 1)
        self.assertEqual(out["BRCA1"]["block_rate"], 0.5)
        self.assertEqual(out["TP53"]["blocked"], 1)

    def test_rollup_unspecified_dimension_bucket(self) -> None:
        rec = cov.compute_coverage("GRCh38-3-4-A-G", [], variant_class="missense")
        out = cov.rollup([rec], "gene")
        self.assertIn("(unspecified)", out)

    def test_rollup_rejects_unknown_dimension(self) -> None:
        with self.assertRaises(ValueError):
            cov.rollup(self._records(), "nonsense")

    def test_summarize_covers_every_dimension(self) -> None:
        summary = cov.summarize(self._records())
        self.assertEqual(summary["total"], 3)
        self.assertEqual(summary["blocked"], 2)
        self.assertEqual(set(summary["by"]), set(cov.DIMENSIONS))

    def test_summarize_reads_dict_rows_with_db_column_name(self) -> None:
        # DB rows carry ``missing_criteria``; summarize must read either spelling.
        rows = [
            {"variant_key": "GRCh38-1-1-A-G", "gene": "BRCA1", "blocked": True,
             "missing_criteria": ["functional"]},
            {"variant_key": "GRCh38-1-2-A-G", "gene": "BRCA1", "blocked": False,
             "missing_categories": []},
        ]
        summary = cov.summarize(rows)
        self.assertEqual(summary["total"], 2)
        self.assertEqual(summary["blocked"], 1)
        self.assertEqual(summary["by"]["gene"]["BRCA1"]["missing_categories"], {"functional": 1})


if __name__ == "__main__":
    unittest.main()
