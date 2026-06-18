"""Standard-library unit tests for the evidence-aware harness summaries.

These exercise the pure metric helpers added to ``validation/harness.py``
(class/per-tier recall, matched-vs-unmatched concordance, evidence coverage,
per-provider improvement, and the ancestry-vs-panel grouping split). They build
tiny in-memory fixtures so they never depend on the large real fixtures and run
fast under the shared ``unittest discover``.

Run from the project root (the ``ReClass Model/`` folder)::

    python3 -m unittest tests.test_harness_summary -v
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from validation import harness as H


# --------------------------------------------------------------------------- #
# Builders
# --------------------------------------------------------------------------- #


def _crit(name, direction, strength, source=None):
    c = {"criterion": name, "direction": direction, "strength": strength}
    if source is not None:
        c["source"] = source
    return c


def _case(cid, gene, ancestry, expected, signals=None, enrichment=None):
    case = {
        "id": cid,
        "gene": gene,
        "ancestry": ancestry,
        "expected": expected,
        "signals": signals or {"criteria": []},
    }
    if enrichment is not None:
        case["enrichment"] = enrichment
    return case


def _result(expected, predicted, **flags):
    """A scored result row as produced by evaluate(), with evidence flags."""
    base = {
        "id": flags.get("id", "x"),
        "gene": flags.get("gene", "G"),
        "ancestry": flags.get("ancestry", "European"),
        "group_kind": flags.get("group_kind", "ancestry"),
        "expected": expected,
        "predicted": predicted,
        "points": 0.0,
        "match": expected == predicted,
        "serious": H._is_serious(expected, predicted),
        "n_criteria": flags.get("n_criteria", 0),
        "has_revel": flags.get("has_revel", False),
        "has_gnomad_af": flags.get("has_gnomad_af", False),
        "has_clingen": flags.get("has_clingen", False),
        "enriched": flags.get("enriched", False),
        "matched": flags.get("matched", None),
        "providers": flags.get("providers", []),
    }
    return base


# --------------------------------------------------------------------------- #
# Grouping classification (task 5)
# --------------------------------------------------------------------------- #


class TestGroupingKind(unittest.TestCase):
    def test_true_ancestry(self):
        self.assertEqual(H.grouping_kind("European"), "ancestry")
        self.assertEqual(H.grouping_kind("east asian"), "ancestry")
        self.assertEqual(H.grouping_kind("African American"), "ancestry")

    def test_vcep_panel_is_not_ancestry(self):
        self.assertEqual(H.grouping_kind("Phenylketonuria VCEP"), "panel")
        self.assertEqual(H.grouping_kind("Myeloid Malignancy VCEP"), "panel")
        self.assertEqual(H.grouping_kind("Some Disorder Panel"), "panel")

    def test_unspecified(self):
        self.assertEqual(H.grouping_kind("Unspecified"), "unspecified")
        self.assertEqual(H.grouping_kind(None), "unspecified")
        self.assertEqual(H.grouping_kind(""), "unspecified")
        # Unknown free-text is treated as unspecified, never mislabeled ancestry.
        self.assertEqual(H.grouping_kind("Cardiomyopathy cohort 7"), "unspecified")


# --------------------------------------------------------------------------- #
# case_evidence flag extraction
# --------------------------------------------------------------------------- #


class TestCaseEvidence(unittest.TestCase):
    def test_signal_flags(self):
        case = _case("1", "BRCA1", "European", "Pathogenic", signals={
            "criteria": [_crit("PVS1", "pathogenic", "very_strong")],
            "revel": 0.9, "gnomad_af": 1e-5,
        })
        ev = H.case_evidence(case)
        self.assertEqual(ev["n_criteria"], 1)
        self.assertTrue(ev["has_revel"])
        self.assertTrue(ev["has_gnomad_af"])
        self.assertFalse(ev["has_clingen"])
        self.assertFalse(ev["enriched"])
        self.assertIsNone(ev["matched"])

    def test_clingen_from_criteria_source(self):
        case = _case("1", "PAH", "Phenylketonuria VCEP", "Pathogenic", signals={
            "criteria": [_crit("PS3", "pathogenic", "strong", source="clingen")],
        })
        self.assertTrue(H.case_evidence(case)["has_clingen"])

    def test_enrichment_block(self):
        case = _case("1", "GENE", "Unspecified", "Benign", signals={"criteria": []},
                     enrichment={"clingen_variation_id_match": True,
                                 "providers": ["clingen_erepo"]})
        ev = H.case_evidence(case)
        self.assertTrue(ev["enriched"])
        self.assertTrue(ev["matched"])
        self.assertTrue(ev["has_clingen"])  # provider implies clingen evidence
        self.assertEqual(ev["providers"], ["clingen_erepo"])

    def test_fallback_enrichment_uses_authoritative_matched_flag(self):
        case = _case("1", "GENE", "Unspecified", "Benign", signals={"criteria": []},
                     enrichment={"clingen_variation_id_match": False,
                                 "matched": True,
                                 "route": "hgvs_g",
                                 "providers": ["clingen_erepo"]})
        ev = H.case_evidence(case)
        self.assertTrue(ev["enriched"])
        self.assertTrue(ev["matched"])
        self.assertTrue(ev["has_clingen"])

    def test_unmatched_enrichment(self):
        case = _case("1", "GENE", "Unspecified", "Benign", signals={"criteria": []},
                     enrichment={"clingen_variation_id_match": False, "providers": []})
        ev = H.case_evidence(case)
        self.assertTrue(ev["enriched"])
        self.assertFalse(ev["matched"])


# --------------------------------------------------------------------------- #
# Recall (task 1)
# --------------------------------------------------------------------------- #


class TestClassRecall(unittest.TestCase):
    def test_class_recall(self):
        results = [
            _result("Pathogenic", "Pathogenic"),
            _result("Pathogenic", "VUS"),
            _result("Likely Pathogenic", "Likely Pathogenic"),
            _result("Benign", "Benign"),
            _result("Likely Benign", "VUS"),
            _result("VUS", "VUS"),
        ]
        cr = H.class_recall(results)
        # pathogenic class: Patho + LP -> 2/3 reproduced
        self.assertEqual(cr["pathogenic"]["n"], 3)
        self.assertAlmostEqual(cr["pathogenic"]["recall"], 2.0 / 3.0)
        # benign class: B + LB -> 1/2 reproduced
        self.assertEqual(cr["benign"]["n"], 2)
        self.assertAlmostEqual(cr["benign"]["recall"], 0.5)
        # vus exact match
        self.assertEqual(cr["vus"]["n"], 1)
        self.assertAlmostEqual(cr["vus"]["recall"], 1.0)

    def test_empty_class_recall_is_zero(self):
        cr = H.class_recall([])
        for key in ("pathogenic", "benign", "vus"):
            self.assertEqual(cr[key]["n"], 0)
            self.assertEqual(cr[key]["recall"], 0.0)


# --------------------------------------------------------------------------- #
# Matched vs unmatched (task 2)
# --------------------------------------------------------------------------- #


class TestMatchedUnmatched(unittest.TestCase):
    def test_none_without_enrichment(self):
        results = [_result("Pathogenic", "Pathogenic")]
        self.assertIsNone(H.matched_unmatched_concordance(results))

    def test_split(self):
        results = [
            _result("Pathogenic", "Pathogenic", enriched=True, matched=True),
            _result("Pathogenic", "Pathogenic", enriched=True, matched=True),
            _result("Pathogenic", "VUS", enriched=True, matched=False),
            _result("Benign", "VUS", enriched=True, matched=False),
        ]
        mu = H.matched_unmatched_concordance(results)
        self.assertEqual(mu["matched"]["n"], 2)
        self.assertAlmostEqual(mu["matched"]["concordance"], 1.0)
        self.assertEqual(mu["unmatched"]["n"], 2)
        self.assertAlmostEqual(mu["unmatched"]["concordance"], 0.0)


# --------------------------------------------------------------------------- #
# Coverage (task 3)
# --------------------------------------------------------------------------- #


class TestCoverage(unittest.TestCase):
    def test_coverage_counts_and_buckets(self):
        results = [
            _result("Pathogenic", "Pathogenic", n_criteria=2, has_revel=True,
                    has_clingen=True, enriched=True, matched=True),
            _result("Benign", "Benign", n_criteria=0, has_gnomad_af=True),
            _result("VUS", "VUS", n_criteria=5),
        ]
        cov = H.coverage_from_results(results)
        self.assertEqual(cov["cases"], 3)
        self.assertEqual(cov["with_criteria"], 2)
        self.assertEqual(cov["with_revel"], 1)
        self.assertEqual(cov["with_gnomad_af"], 1)
        self.assertEqual(cov["with_clingen"], 1)
        self.assertEqual(cov["with_enrichment"], 1)
        self.assertEqual(cov["criteria_buckets"]["0"], 1)
        self.assertEqual(cov["criteria_buckets"]["1-2"], 1)
        self.assertEqual(cov["criteria_buckets"]["5+"], 1)


# --------------------------------------------------------------------------- #
# Provider coverage / improvement (task 4)
# --------------------------------------------------------------------------- #


class TestProviderCoverage(unittest.TestCase):
    def test_present_vs_absent_delta(self):
        results = [
            # has clingen + matches
            _result("Pathogenic", "Pathogenic", has_clingen=True),
            _result("Pathogenic", "Pathogenic", has_clingen=True),
            # lacks clingen + misses
            _result("Pathogenic", "VUS"),
            _result("Benign", "VUS"),
        ]
        pc = H.provider_coverage(results)
        cg = pc["clingen"]
        self.assertEqual(cg["present_n"], 2)
        self.assertAlmostEqual(cg["present_concordance"], 1.0)
        self.assertEqual(cg["absent_n"], 2)
        self.assertAlmostEqual(cg["absent_concordance"], 0.0)
        self.assertAlmostEqual(cg["concordance_delta"], 1.0)
        # per-class breakdown over the cases that HAVE the provider
        self.assertEqual(cg["by_class"]["pathogenic"]["n"], 2)
        self.assertAlmostEqual(cg["by_class"]["pathogenic"]["concordance"], 1.0)

    def test_all_providers_present(self):
        pc = H.provider_coverage([_result("VUS", "VUS")])
        self.assertEqual(set(pc), {"clingen", "revel", "gnomad_af"})


# --------------------------------------------------------------------------- #
# End-to-end through evaluate + compute_metrics + render
# --------------------------------------------------------------------------- #


class TestEndToEnd(unittest.TestCase):
    def _benchmark(self):
        return {
            "benchmark": "mini_v1",
            "cases": [
                _case("P1", "BRCA1", "European", "Pathogenic", signals={
                    "criteria": [_crit("PVS1", "pathogenic", "very_strong"),
                                 _crit("PS1", "pathogenic", "strong")]}),
                _case("B1", "AFX", "African", "Benign", signals={"gnomad_af": 0.2}),
                _case("CG1", "PAH", "Phenylketonuria VCEP", "Pathogenic", signals={
                    "criteria": [_crit("PS3", "pathogenic", "strong",
                                       source="clingen"),
                                 _crit("PM3", "pathogenic", "moderate",
                                       source="clingen")]}),
                _case("U1", "GENE", "Unspecified", "Benign", signals={"criteria": []},
                      enrichment={"clingen_variation_id_match": False,
                                  "providers": []}),
            ],
        }

    def test_evaluate_attaches_flags(self):
        results = H.evaluate(self._benchmark())
        by_id = {r["id"]: r for r in results}
        self.assertEqual(by_id["P1"]["group_kind"], "ancestry")
        self.assertEqual(by_id["CG1"]["group_kind"], "panel")
        self.assertEqual(by_id["U1"]["group_kind"], "unspecified")
        self.assertTrue(by_id["CG1"]["has_clingen"])
        self.assertTrue(by_id["B1"]["has_gnomad_af"])
        self.assertTrue(by_id["U1"]["enriched"])

    def test_compute_metrics_has_evidence_blocks(self):
        results = H.evaluate(self._benchmark())
        m = H.compute_metrics(results)
        for key in ("class_recall", "per_tier_recall", "coverage",
                    "provider_coverage", "matched_unmatched"):
            self.assertIn(key, m)
        # one case carries enrichment -> matched/unmatched present
        self.assertIsNotNone(m["matched_unmatched"])
        # by_ancestry entries are tagged with a kind
        kinds = {v["kind"] for v in m["by_ancestry"].values()}
        self.assertTrue({"ancestry", "panel", "unspecified"} & kinds)

    def test_markdown_has_new_sections(self):
        results = H.evaluate(self._benchmark())
        m = H.compute_metrics(results)
        md = "\n".join(
            H._recall_markdown(m) + H._matched_markdown(m)
            + H._coverage_markdown(m) + H._provider_markdown(m)
            + H._stratification_markdown(m))
        self.assertIn("Recall by evidence class", md)
        self.assertIn("Concordance by evidence match", md)
        self.assertIn("Evidence coverage", md)
        self.assertIn("Provider coverage & improvement", md)
        self.assertIn("Concordance by ancestry", md)
        self.assertIn("Concordance by VCEP / panel group", md)

    def test_matched_section_absent_without_enrichment(self):
        bench = {
            "benchmark": "noenrich_v1",
            "cases": [_case("P1", "BRCA1", "European", "Pathogenic", signals={
                "criteria": [_crit("PVS1", "pathogenic", "very_strong")]})],
        }
        m = H.compute_metrics(H.evaluate(bench))
        self.assertIsNone(m["matched_unmatched"])
        self.assertEqual(H._matched_markdown(m), [])


if __name__ == "__main__":
    unittest.main()
