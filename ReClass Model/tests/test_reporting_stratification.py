"""Tests for separating ancestry/population from VCEP/panel fields in reporting.

job1 task 5: true genetic-ancestry/population-stratification fields must be reported
distinctly from VCEP/expert-panel grouping fields, so a panel name carried in a
legacy ``ancestry`` field is never presented as an ancestry stratum.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from reporting.common import stratification_block
from reporting import build_reviewer_report, render_reviewer_markdown


class TestStratificationBlock(unittest.TestCase):
    def test_distinct_fields_pass_through(self):
        block = stratification_block({"population": "African", "vcep_group": "PAH VCEP",
                                      "ancestry": "African"})
        self.assertEqual(block["population"], "African")
        self.assertEqual(block["vcep_group"], "PAH VCEP")

    def test_legacy_panel_is_not_an_ancestry(self):
        # Old conflated record: a VCEP name in `ancestry`, no dedicated fields.
        block = stratification_block({"ancestry": "Phenylketonuria VCEP"})
        self.assertIsNone(block["population"])
        self.assertEqual(block["vcep_group"], "Phenylketonuria VCEP")
        self.assertTrue(block["legacy_ancestry_is_panel"])

    def test_legacy_real_ancestry_backfills_population(self):
        block = stratification_block({"ancestry": "European"})
        self.assertEqual(block["population"], "European")
        self.assertIsNone(block["vcep_group"])
        self.assertFalse(block["legacy_ancestry_is_panel"])

    def test_unspecified_legacy_is_neither(self):
        block = stratification_block({"ancestry": "Unspecified"})
        self.assertIsNone(block["population"])
        self.assertIsNone(block["vcep_group"])


class TestReviewerStratification(unittest.TestCase):
    def _receipt(self, **extra):
        receipt = {"classification_id": "c1", "variant_key": "GRCh38-1-100-A-G",
                   "tier": "VUS", "total_points": 0, "engine_version": "1.0.0",
                   "reconstruction_hash": "h", "contributions": [], "overrides": [],
                   "signed_off_by": None}
        receipt.update(extra)
        return receipt

    def test_report_carries_distinct_stratification(self):
        report = build_reviewer_report(
            classification=self._receipt(population="African", vcep_group="PAH VCEP"))
        strat = report["stratification"]
        self.assertEqual(strat["population"], "African")
        self.assertEqual(strat["vcep_group"], "PAH VCEP")

    def test_explicit_stratification_arg_overrides(self):
        report = build_reviewer_report(
            classification=self._receipt(),
            stratification={"population": "East Asian", "vcep_group": None})
        self.assertEqual(report["stratification"]["population"], "East Asian")

    def test_markdown_renders_both_families_separately(self):
        report = build_reviewer_report(
            classification=self._receipt(population="African", vcep_group="PAH VCEP"))
        md = render_reviewer_markdown(report)
        self.assertIn("Population / cohort stratification", md)
        self.assertIn("**Population (ancestry):** African", md)
        self.assertIn("**VCEP / expert-panel group:** PAH VCEP", md)

    def test_markdown_flags_legacy_panel_conflation(self):
        report = build_reviewer_report(
            classification=self._receipt(ancestry="Phenylketonuria VCEP"))
        md = render_reviewer_markdown(report)
        self.assertIn("is a panel name", md)


if __name__ == "__main__":
    unittest.main()
