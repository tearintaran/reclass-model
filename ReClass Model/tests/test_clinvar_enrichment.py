"""Unit tests for ClinVar -> ClinGen enrichment (evidence/enrich_clinvar.py).

Covers criteria-append behavior, label/field preservation, per-case enrichment
metadata, and fixture-level summary counts -- all on tiny in-memory fixtures so the
test is fast and has no file dependency.

Run from ``ReClass Model/``:

    ../.venv/bin/python -m unittest discover -s tests -v
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from evidence.clingen import ClinGenEvidenceProvider, ClinGenIndex
from evidence.enrich_clinvar import BENCHMARK_NAME, build_enriched, enrich_case


def _cg_case(case_id, clinvar_id, expected, criteria):
    return {
        "id": case_id,
        "gene": "GENE",
        "expected": expected,
        "signals": {"criteria": criteria},
        "provenance": {"source": "ClinGen ERepo", "clinvar_id": clinvar_id},
    }


def _crit(name, direction, strength):
    return {"criterion": name, "direction": direction, "strength": strength,
            "source": "clingen", "version": "ERepo"}


# ClinGen side: variation 200 (Pathogenic, PVS1), variation 300 (Benign, BA1).
CLINGEN_CASES = [
    _cg_case("CG-200", "200", "Pathogenic", [_crit("PVS1", "pathogenic", "very_strong")]),
    _cg_case("CG-300", "300", "Benign", [_crit("BA1", "benign", "stand_alone")]),
]


def _cv_case(case_id, variation_id, expected, signals=None):
    return {
        "id": case_id,
        "gene": "GENE",
        "ancestry": "Unspecified",
        "expected": expected,
        "signals": signals if signals is not None else {"criteria": []},
        "locus": {"chrom": "1", "pos": 1, "ref": "A", "alt": "G", "snv": True, "missense": True},
        "provenance": {"source": "ClinVar", "variation_id": variation_id},
    }


# ClinVar side: 200 matches, 300 matches, 999 does not.
CLINVAR = {
    "benchmark": "clinvar_real_v1",
    "engine_version": "1.0.0",
    "cases": [
        _cv_case("CV-200", "200", "Pathogenic", {"criteria": [], "revel": 0.99, "gnomad_af": 1e-6}),
        _cv_case("CV-300", "300", "Benign", {"criteria": [], "gnomad_af": 0.2}),
        _cv_case("CV-999", "999", "VUS", {"criteria": []}),
    ],
}


def _provider():
    return ClinGenEvidenceProvider(ClinGenIndex.from_cases(CLINGEN_CASES))


class TestEnrichCase(unittest.TestCase):
    def test_matched_case_appends_criteria_with_provenance(self):
        out = enrich_case(CLINVAR["cases"][0], _provider())
        criteria = out["signals"]["criteria"]
        self.assertEqual(len(criteria), 1)
        self.assertEqual(criteria[0]["criterion"], "PVS1")
        self.assertEqual(criteria[0]["raw"]["clingen_case_id"], "CG-200")
        self.assertEqual(out["enrichment"]["clingen_variation_id_match"], True)
        self.assertEqual(out["enrichment"]["clingen_case_id"], "CG-200")
        self.assertEqual(out["enrichment"]["providers"], ["clingen_erepo"])
        self.assertEqual(out["enrichment"]["criteria_added"], 1)

    def test_preserves_original_signals_and_labels(self):
        out = enrich_case(CLINVAR["cases"][0], _provider())
        # Original non-criteria signals survive.
        self.assertEqual(out["signals"]["revel"], 0.99)
        self.assertEqual(out["signals"]["gnomad_af"], 1e-6)
        # Expected label is the ClinVar label, never the ClinGen label.
        self.assertEqual(out["expected"], "Pathogenic")
        # Original fields preserved.
        self.assertIn("locus", out)
        self.assertEqual(out["provenance"]["variation_id"], "200")

    def test_does_not_mutate_input(self):
        case = CLINVAR["cases"][0]
        before = len(case["signals"]["criteria"])
        enrich_case(case, _provider())
        self.assertEqual(len(case["signals"]["criteria"]), before)
        self.assertNotIn("enrichment", case)

    def test_unmatched_case_records_no_match(self):
        out = enrich_case(CLINVAR["cases"][2], _provider())
        self.assertEqual(out["signals"]["criteria"], [])
        self.assertEqual(out["enrichment"]["clingen_variation_id_match"], False)
        self.assertIsNone(out["enrichment"]["clingen_case_id"])
        self.assertEqual(out["enrichment"]["providers"], [])
        self.assertEqual(out["enrichment"]["criteria_added"], 0)
        self.assertEqual(out["enrichment"]["warnings"], ["no_clingen_match"])


class TestBuildEnriched(unittest.TestCase):
    def setUp(self):
        self.enriched = build_enriched(CLINVAR, _provider())

    def test_top_level_shape(self):
        self.assertEqual(self.enriched["benchmark"], BENCHMARK_NAME)
        self.assertEqual(len(self.enriched["cases"]), 3)
        self.assertIn("enrichment_summary", self.enriched)

    def test_summary_counts(self):
        s = self.enriched["enrichment_summary"]
        self.assertEqual(s["source"], "clinvar_real_v1 + clingen_real_v1")
        self.assertEqual(s["total_cases"], 3)
        self.assertEqual(s["clingen_variation_id_matches"], 2)
        self.assertEqual(s["unmatched"], 1)
        self.assertEqual(s["criteria_added_cases"], 2)
        self.assertEqual(s["criteria_added_total"], 2)
        self.assertEqual(s["clingen_index_size"], 2)

    def test_summary_matches_per_case_metadata(self):
        s = self.enriched["enrichment_summary"]
        matched = sum(
            1 for c in self.enriched["cases"]
            if c["enrichment"]["clingen_variation_id_match"]
        )
        self.assertEqual(matched, s["clingen_variation_id_matches"])

    def test_every_case_has_enrichment_block(self):
        for c in self.enriched["cases"]:
            self.assertIn("enrichment", c)
            self.assertIn("warnings", c["enrichment"])

    def test_expected_labels_unchanged(self):
        expected = {c["id"]: c["expected"] for c in CLINVAR["cases"]}
        for c in self.enriched["cases"]:
            self.assertEqual(c["expected"], expected[c["id"]])


# --------------------------------------------------------------------------- #
# Match-type accounting + identity audit (Part A, tasks 6-7)                  #
# --------------------------------------------------------------------------- #
# ClinGen records that DO carry coordinates, so canonical-key matching can fire.
CLINGEN_WITH_LOCI = [
    _cg_case("CG-200", "200", "Pathogenic", [_crit("PVS1", "pathogenic", "very_strong")]),
]
CLINGEN_WITH_LOCI[0]["locus"] = {"chrom": "1", "pos": 500, "ref": "A", "alt": "G"}


class TestMatchTypeAccounting(unittest.TestCase):
    def test_summary_counts_match_routes_separately(self):
        enriched = build_enriched(CLINVAR, _provider())
        s = enriched["enrichment_summary"]
        # Both CV-200 and CV-300 match by Variation ID; CV-999 does not match.
        self.assertEqual(s["match_by_variation_id"], 2)
        self.assertEqual(s["match_by_canonical_snv_key"], 0)
        self.assertEqual(s["match_by_reference_indel_key"], 0)
        self.assertEqual(s["matched_total"], 2)
        # Back-compatible field still equals the Variation ID count.
        self.assertEqual(s["clingen_variation_id_matches"], 2)
        self.assertEqual(s["canonical_key_matches"], 0)

    def test_canonical_snv_key_match_counted(self):
        # ClinGen indexed by locus; ClinVar case with a non-matching Variation ID but
        # a coordinate that matches -> counted as a canonical SNV-key match.
        prov = ClinGenEvidenceProvider(ClinGenIndex.from_cases(CLINGEN_WITH_LOCI))
        clinvar = {
            "benchmark": "x", "engine_version": "1.0.0",
            "cases": [_cv_case("CV-Z", "777", "Pathogenic", {"criteria": []})],
        }
        clinvar["cases"][0]["locus"] = {"chrom": "1", "pos": 500, "ref": "A", "alt": "G",
                                        "snv": True}
        enriched = build_enriched(clinvar, prov)
        s = enriched["enrichment_summary"]
        self.assertEqual(s["match_by_canonical_snv_key"], 1)
        self.assertEqual(s["match_by_variation_id"], 0)
        self.assertEqual(s["matched_total"], 1)
        case = enriched["cases"][0]
        self.assertEqual(case["enrichment"]["match_detail"], "canonical_snv_key")
        self.assertFalse(case["enrichment"]["clingen_variation_id_match"])
        self.assertEqual(case["enrichment"]["criteria_added"], 1)

    def test_failed_normalization_is_recorded(self):
        # A locus with an invalid allele must be flagged, never a silent non-match.
        clinvar = {
            "benchmark": "x", "engine_version": "1.0.0",
            "cases": [_cv_case("CV-BAD", "-", "VUS", {"criteria": []})],
        }
        clinvar["cases"][0]["locus"] = {"chrom": "1", "pos": 1, "ref": "A", "alt": "N*"}
        enriched = build_enriched(clinvar, _provider())
        s = enriched["enrichment_summary"]
        self.assertEqual(s["normalization_failed"], 1)
        self.assertTrue(enriched["cases"][0]["enrichment"]["normalization_failed"])

    def test_identity_audit_present_in_summary(self):
        s = build_enriched(CLINVAR, _provider())["enrichment_summary"]
        self.assertIn("identity_audit", s)
        audit = s["identity_audit"]
        self.assertIn("reference_free", audit)
        self.assertEqual(audit["reference_available"], False)
        # CV-200/CV-300/CV-999 all carry SNV loci.
        self.assertEqual(audit["snv"], 3)


if __name__ == "__main__":
    unittest.main()
