"""Unit tests for batch evidence importers (job1 task 4).

Pure, offline. Verifies that bulk evidence routes through the upstream adapters with
provenance, and — critically — that **no raw PHI** survives into the de-identified
evidence the importer produces.
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ingest import batch_import as bi  # noqa: E402


class TestPhiScrub(unittest.TestCase):
    def test_drops_known_phi_fields(self) -> None:
        clean, dropped = bi.scrub_phi(
            {"mrn": "X1", "patient_name": "Jane", "result": "damaging", "oddspath": 25.0}
        )
        self.assertEqual(clean, {"result": "damaging", "oddspath": 25.0})
        self.assertEqual(sorted(dropped), ["mrn", "patient_name"])

    def test_drops_nested_phi_containers(self) -> None:
        clean, dropped = bi.scrub_phi({"patient": {"name": "Jane"}, "result": "normal"})
        self.assertEqual(clean, {"result": "normal"})
        self.assertEqual(dropped, ["patient"])

    def test_keeps_deidentified_evidence_verbatim(self) -> None:
        payload = {"result": "damaging", "oddspath": 30.0, "assay": "MAVE"}
        clean, dropped = bi.scrub_phi(payload)
        self.assertEqual(clean, payload)
        self.assertEqual(dropped, [])


class TestImportBatch(unittest.TestCase):
    def test_functional_import_emits_ps3_and_drops_phi(self) -> None:
        result = bi.import_batch(
            "functional",
            [{"variant_key": "GRCh38-1-100-A-G", "gene": "BRCA1", "mrn": "SECRET",
              "result": "damaging", "oddspath": 25.0}],
            access_date="2026-06-17",
        )
        report = result["report"]
        self.assertEqual(report["total"], 1)
        self.assertEqual(report["called"], 1)
        self.assertEqual(report["phi_fields_dropped"], 1)
        entry = report["records"][0]
        self.assertEqual(entry["criteria"], ["PS3"])
        self.assertEqual(entry["phi_fields_dropped"], ["mrn"])

        # The produced bundle carries no PHI value anywhere, and no mrn key in the
        # de-identified evidence record (the only mention of "mrn" is the warning that
        # names which field was dropped).
        bundle = result["bundles"][0]
        self.assertNotIn("SECRET", bundle.to_json())
        self.assertNotIn("mrn", bundle.source_records[0]["record"])
        self.assertIn("phi_fields_dropped:mrn", bundle.warnings)

    def test_cohort_import_emits_ps4_with_denominator(self) -> None:
        result = bi.import_batch(
            "cohort",
            [{"variant_key": "GRCh38-2-200-C-T", "case_count": 40, "case_total": 100,
              "control_count": 2, "control_total": 100, "p_value": 1e-6}],
            access_date="2026-06-17",
        )
        entry = result["report"]["records"][0]
        self.assertEqual(entry["criteria"], ["PS4"])
        self.assertEqual(entry["cohort_counts"]["denominator"], 200)

    def test_phenotype_import_routes_to_pp4(self) -> None:
        result = bi.import_batch(
            "phenotype",
            [{"variant_key": "GRCh38-3-300-G-A", "specificity": "high"}],
        )
        entry = result["report"]["records"][0]
        self.assertEqual(entry["evidence_type"], "phenotype")

    def test_family_import_allows_segregation_and_de_novo(self) -> None:
        result = bi.import_batch(
            "family",
            [
                {"variant_key": "GRCh38-4-400-A-T", "evidence_type": "segregation",
                 "meioses": 7, "segregates": True},
                {"variant_key": "GRCh38-4-401-A-T", "evidence_type": "de_novo",
                 "confirmed_parentage": True, "phenotype_consistent": True},
            ],
        )
        types = {r["evidence_type"] for r in result["report"]["records"]}
        self.assertEqual(types, {"segregation", "de_novo"})

    def test_unknown_source_kind_rejected(self) -> None:
        with self.assertRaises(bi.BatchImportError):
            bi.import_batch("nonsense", [])

    def test_disallowed_evidence_type_for_kind_rejected(self) -> None:
        with self.assertRaises(bi.BatchImportError):
            bi.import_batch(
                "functional",
                [{"variant_key": "GRCh38-1-1-A-G", "evidence_type": "case_control"}],
            )

    def test_malformed_row_recorded_not_raised(self) -> None:
        result = bi.import_batch("functional", ["not-a-dict"])
        self.assertEqual(result["report"]["records"][0]["status"], "malformed")


if __name__ == "__main__":
    unittest.main()
