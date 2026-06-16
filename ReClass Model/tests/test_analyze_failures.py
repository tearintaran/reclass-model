"""Standard-library unit tests for ``validation/analyze_failures.py``.

These tests build a tiny in-memory report+fixture pair so they never depend on
the large real fixtures being present. The one test that touches a real report
is guarded with ``unittest.skipUnless`` so the shared ``unittest discover`` never
breaks for another agent.

Run from the project root (the ``ReClass Model/`` folder):

    python3 -m unittest tests.test_analyze_failures -v
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from validation import analyze_failures as AF

MODEL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORTS_DIR = os.path.join(MODEL_DIR, "validation", "reports")


def _report_case(cid, gene, group, expected, predicted, points, match, serious):
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


def _fixture_case(cid, gene, group, expected, criteria=None, revel=None,
                  gnomad_af=None, provenance=None):
    signals = {"criteria": criteria or []}
    if revel is not None:
        signals["revel"] = revel
    if gnomad_af is not None:
        signals["gnomad_af"] = gnomad_af
    case = {
        "id": cid,
        "gene": gene,
        "ancestry": group,
        "expected": expected,
        "signals": signals,
    }
    if provenance is not None:
        case["provenance"] = provenance
    return case


def _crit(name, direction, strength):
    return {"criterion": name, "direction": direction, "strength": strength}


def _tiny_pair():
    """A controlled report/fixture pair covering each gap category."""
    report = {
        "benchmark": "tiny_v1",
        "engine_version": "9.9.9",
        "run_utc": "2026-01-01T00:00:00+00:00",
        "gate_pass": True,
        "metrics": {"n": 6, "definitive_concordance": 0.5,
                    "overall_concordance": 0.5, "serious_count": 2},
        "cases": [
            # correct -> ignored by the mismatch rollups
            _report_case("A1", "BRCA1", "G1", "Pathogenic", "Pathogenic",
                         10.0, True, False),
            # under-pathogenic, no pathogenic criteria -> missing_pathogenic_criteria (serious)
            _report_case("B1", "BRCA1", "G1", "Pathogenic", "VUS",
                         1.0, False, True),
            # another identical-category mismatch -> same gap bucket
            _report_case("B2", "BRCA1", "G2", "Pathogenic", "VUS",
                         0.0, False, False),
            # under-pathogenic but criteria present -> insufficient_pathogenic_strength
            _report_case("C1", "TP53", "G1", "Pathogenic", "Likely Pathogenic",
                         8.0, False, False),
            # over-pathogenic, no benign criteria -> missing_benign_criteria (serious)
            _report_case("D1", "MLH1", "G2", "Benign", "VUS",
                         1.0, False, True),
            # over-pathogenic, benign criteria present -> insufficient_benign_strength
            _report_case("E1", "MLH1", "G2", "Benign", "Likely Benign",
                         -2.0, False, False),
        ],
    }
    fixture = {
        "benchmark": "tiny_v1",
        "cases": [
            _fixture_case("A1", "BRCA1", "G1", "Pathogenic",
                          criteria=[_crit("PVS1", "pathogenic", "very_strong")]),
            _fixture_case("B1", "BRCA1", "G1", "Pathogenic",
                          revel=0.9, gnomad_af=0.0001,
                          provenance={"source": "ClinVar", "variation_id": "111"}),
            _fixture_case("B2", "BRCA1", "G2", "Pathogenic", revel=0.8),
            _fixture_case("C1", "TP53", "G1", "Pathogenic",
                          criteria=[_crit("PM2", "pathogenic", "moderate"),
                                    _crit("PP3", "pathogenic", "supporting")]),
            _fixture_case("D1", "MLH1", "G2", "Benign", gnomad_af=0.2),
            _fixture_case("E1", "MLH1", "G2", "Benign",
                          criteria=[_crit("BS1", "benign", "strong")]),
        ],
    }
    return report, fixture


class TestTierAndGapClassification(unittest.TestCase):
    def test_tier_rank_ordering(self):
        self.assertGreater(AF.tier_rank("Pathogenic"), AF.tier_rank("VUS"))
        self.assertGreater(AF.tier_rank("VUS"), AF.tier_rank("Benign"))
        self.assertEqual(AF.tier_rank("nonsense-tier"), 2)  # neutral fallback

    def test_classify_missing_pathogenic(self):
        d, cat = AF.classify_gap("Pathogenic", "VUS", [], {"revel": 0.9})
        self.assertEqual(d, "under-pathogenic")
        self.assertEqual(cat, "missing_pathogenic_criteria")

    def test_classify_insufficient_pathogenic_strength(self):
        crit = [_crit("PM2", "pathogenic", "moderate")]
        d, cat = AF.classify_gap("Pathogenic", "Likely Pathogenic", crit, {})
        self.assertEqual(d, "under-pathogenic")
        self.assertEqual(cat, "insufficient_pathogenic_strength")

    def test_classify_missing_benign(self):
        d, cat = AF.classify_gap("Benign", "VUS", [], {"gnomad_af": 0.2})
        self.assertEqual(d, "over-pathogenic")
        self.assertEqual(cat, "missing_benign_criteria")

    def test_classify_insufficient_benign_strength(self):
        crit = [_crit("BS1", "benign", "strong")]
        d, cat = AF.classify_gap("Benign", "Likely Benign", crit, {})
        self.assertEqual(d, "over-pathogenic")
        self.assertEqual(cat, "insufficient_benign_strength")

    def test_direction_falls_back_to_name_prefix(self):
        # No 'direction' field -> classify by ACMG name prefix.
        self.assertTrue(AF.has_pathogenic_criteria([{"criterion": "PVS1"}]))
        self.assertTrue(AF.has_benign_criteria([{"criterion": "BA1"}]))
        self.assertFalse(AF.has_pathogenic_criteria([{"criterion": "BP4"}]))


class TestAnalyze(unittest.TestCase):
    def setUp(self):
        self.report, self.fixture = _tiny_pair()
        self.analysis = AF.analyze(self.report, self.fixture, benchmark="tiny_v1")

    def test_totals(self):
        t = self.analysis["totals"]
        self.assertEqual(t["report_cases"], 6)
        self.assertEqual(t["mismatches"], 5)  # all but A1
        self.assertEqual(t["serious"], 2)     # B1, D1
        self.assertEqual(t["missing_in_fixture"], 0)

    def test_serious_extraction(self):
        serious = self.analysis["serious_errors"]
        self.assertEqual({s["id"] for s in serious}, {"B1", "D1"})
        b1 = next(s for s in serious if s["id"] == "B1")
        self.assertEqual(b1["expected"], "Pathogenic")
        self.assertEqual(b1["predicted"], "VUS")
        self.assertEqual(b1["points"], 1.0)
        # provenance link is derived from variation_id
        self.assertIn("111", b1["provenance_link"])
        # signals are surfaced, criteria empty here
        self.assertEqual(b1["criteria"], [])
        self.assertEqual(b1["signals"].get("revel"), 0.9)

    def test_serious_with_criteria_summary(self):
        # Add a serious case that has criteria, confirm the compact summary.
        report, fixture = _tiny_pair()
        report["cases"][3]["serious"] = True  # C1 has PM2/PP3
        analysis = AF.analyze(report, fixture)
        c1 = next(s for s in analysis["serious_errors"] if s["id"] == "C1")
        self.assertIn("PM2(moderate)", c1["criteria"])
        self.assertIn("PP3(supporting)", c1["criteria"])

    def test_pair_rollup(self):
        pairs = {(r["expected"], r["predicted"]): r
                 for r in self.analysis["rollups"]["by_pair"]}
        self.assertEqual(pairs[("Pathogenic", "VUS")]["count"], 2)   # B1, B2
        self.assertEqual(pairs[("Pathogenic", "VUS")]["serious"], 1)  # only B1
        self.assertEqual(pairs[("Benign", "VUS")]["count"], 1)        # D1

    def test_gene_and_group_rollups(self):
        genes = {r["gene"]: r for r in self.analysis["rollups"]["by_gene"]}
        self.assertEqual(genes["BRCA1"]["count"], 2)   # B1, B2 (A1 matched)
        self.assertEqual(genes["MLH1"]["count"], 2)    # D1, E1
        self.assertEqual(genes["BRCA1"]["serious"], 1)
        groups = {r["group"]: r for r in self.analysis["rollups"]["by_group"]}
        self.assertEqual(groups["G2"]["count"], 3)     # B2, D1, E1

    def test_evidence_type_rollup(self):
        ev = {r["category"]: r["count"]
              for r in self.analysis["rollups"]["by_evidence_type"]}
        self.assertEqual(ev["missing_pathogenic_criteria"], 2)      # B1, B2
        self.assertEqual(ev["insufficient_pathogenic_strength"], 1)  # C1
        self.assertEqual(ev["missing_benign_criteria"], 1)          # D1
        self.assertEqual(ev["insufficient_benign_strength"], 1)     # E1

    def test_signals_and_criteria_buckets(self):
        sig = self.analysis["rollups"]["signals_present"]
        # B1 has revel+gnomad, B2 revel, D1 gnomad; C1/E1 neither
        self.assertEqual(sig.get("REVEL"), 2)        # B1, B2
        self.assertEqual(sig.get("gnomAD AF"), 2)    # B1, D1
        self.assertEqual(sig.get("neither"), 2)      # C1, E1
        buckets = {r["bucket"]: r["count"]
                   for r in self.analysis["rollups"]["by_criteria_count"]}
        self.assertEqual(buckets.get("0"), 3)        # B1, B2, D1
        self.assertEqual(buckets.get("1-2"), 2)      # C1 (2), E1 (1)

    def test_serious_vs_nonserious(self):
        ss = self.analysis["rollups"]["serious_vs_nonserious"]
        self.assertEqual(ss.get("serious"), 2)
        self.assertEqual(ss.get("non_serious"), 3)

    def test_gap_ranking(self):
        gaps = self.analysis["gaps"]
        # Ranked by count desc: the only count-2 gap leads.
        self.assertEqual(gaps[0]["count"], 2)
        self.assertEqual(gaps[0]["expected"], "Pathogenic")
        self.assertEqual(gaps[0]["predicted"], "VUS")
        self.assertEqual(gaps[0]["category"], "missing_pathogenic_criteria")
        self.assertEqual(gaps[0]["serious"], 1)
        self.assertEqual(gaps[0]["with_revel"], 2)
        # counts are monotonically non-increasing
        counts = [g["count"] for g in gaps]
        self.assertEqual(counts, sorted(counts, reverse=True))
        # every mismatch is accounted for across the gaps
        self.assertEqual(sum(counts), self.analysis["totals"]["mismatches"])
        # each gap carries a human-readable description and examples
        self.assertIn("Pathogenic", gaps[0]["description"])
        self.assertTrue(gaps[0]["example_ids"])

    def test_missing_fixture_is_tolerated(self):
        report, fixture = _tiny_pair()
        fixture["cases"] = [c for c in fixture["cases"] if c["id"] != "B1"]
        analysis = AF.analyze(report, fixture)
        self.assertEqual(analysis["totals"]["missing_in_fixture"], 1)
        # B1 still counted as a mismatch, just with empty criteria
        self.assertEqual(analysis["totals"]["mismatches"], 5)


class TestEvidenceRecommendations(unittest.TestCase):
    def test_recommend_ranks_by_impact(self):
        # 3 cases missing criteria (clingen helps all), 2 lack revel, 1 lacks gnomad.
        mism = [
            {"category": "missing_pathogenic_criteria", "serious": True,
             "has_revel": False, "has_gnomad_af": True},
            {"category": "missing_pathogenic_criteria", "serious": False,
             "has_revel": True, "has_gnomad_af": True},
            {"category": "missing_benign_criteria", "serious": False,
             "has_revel": True, "has_gnomad_af": False},
        ]
        recs = AF.recommend_evidence_sources(mism)
        by_src = {r["source"]: r for r in recs}
        self.assertEqual(by_src["clingen_criteria"]["count"], 3)
        self.assertEqual(by_src["clingen_criteria"]["serious"], 1)
        self.assertEqual(by_src["revel"]["count"], 1)      # only first lacks revel
        self.assertEqual(by_src["gnomad_af"]["count"], 1)  # only third lacks gnomad
        # ranked by count desc -> clingen first
        self.assertEqual(recs[0]["source"], "clingen_criteria")
        counts = [r["count"] for r in recs]
        self.assertEqual(counts, sorted(counts, reverse=True))

    def test_insufficient_strength_not_attributed_to_clingen(self):
        # An insufficient-strength gap already has criteria, so a new criteria
        # source is not the recommended fix; only the absent signals are.
        mism = [
            {"category": "insufficient_pathogenic_strength", "serious": False,
             "has_revel": False, "has_gnomad_af": False},
        ]
        by_src = {r["source"]: r for r in AF.recommend_evidence_sources(mism)}
        self.assertEqual(by_src["clingen_criteria"]["count"], 0)
        self.assertEqual(by_src["revel"]["count"], 1)
        self.assertEqual(by_src["gnomad_af"]["count"], 1)

    def test_analyze_includes_recommendations(self):
        report, fixture = _tiny_pair()
        analysis = AF.analyze(report, fixture, benchmark="tiny_v1")
        recs = analysis["evidence_recommendations"]
        self.assertTrue(recs)
        by_src = {r["source"]: r for r in recs}
        # B1, B2 (missing patho criteria) + D1 (missing benign criteria) = 3
        self.assertEqual(by_src["clingen_criteria"]["count"], 3)
        # every source key is present even if zero
        self.assertEqual(set(by_src), set(AF.EVIDENCE_SOURCES))

    def test_markdown_renders_recommendations(self):
        report, fixture = _tiny_pair()
        analysis = AF.analyze(report, fixture, benchmark="tiny_v1")
        md = AF.render_markdown(analysis)
        self.assertIn("Recommended next evidence source", md)
        self.assertIn("ClinGen", md)


class TestRenderingAndIO(unittest.TestCase):
    def test_markdown_renders(self):
        report, fixture = _tiny_pair()
        analysis = AF.analyze(report, fixture, benchmark="tiny_v1")
        md = AF.render_markdown(analysis)
        self.assertIn("# Failure analysis -- `tiny_v1`", md)
        self.assertIn("Serious errors", md)
        self.assertIn("B1", md)
        self.assertIn("Top evidence/rule gaps", md)

    def test_stdout_summary_renders(self):
        report, fixture = _tiny_pair()
        analysis = AF.analyze(report, fixture, benchmark="tiny_v1")
        summary = AF.render_stdout_summary(analysis)
        self.assertIn("Serious errors: 2", summary)
        self.assertIn("Benchmark: tiny_v1", summary)

    def test_resolve_report_path_special_case(self):
        p = AF.resolve_report_path("synthetic_v1", "/r")
        self.assertTrue(p.endswith("validation_report.json"))
        p2 = AF.resolve_report_path("clinvar_real_v1", "/r")
        self.assertTrue(p2.endswith("validation_report_clinvar_real_v1.json"))

    def test_run_writes_outputs(self):
        # Drive the full run() against a temp model dir with our tiny pair.
        report, fixture = _tiny_pair()
        with tempfile.TemporaryDirectory() as d:
            rdir = os.path.join(d, "validation", "reports")
            fdir = os.path.join(d, "validation", "fixtures")
            os.makedirs(rdir)
            os.makedirs(fdir)
            import json as _json
            with open(os.path.join(rdir, "validation_report_tiny_v1.json"),
                      "w") as fh:
                _json.dump(report, fh)
            with open(os.path.join(fdir, "tiny_v1.json"), "w") as fh:
                _json.dump(fixture, fh)
            analysis = AF.run("tiny_v1", model_dir=d)
            self.assertTrue(os.path.exists(analysis["_md_path"]))
            self.assertTrue(os.path.exists(analysis["_json_path"]))
            with open(analysis["_json_path"]) as fh:
                reloaded = _json.load(fh)
            self.assertEqual(reloaded["totals"]["serious"], 2)


class TestRealReportsIfPresent(unittest.TestCase):
    """Optional smoke checks against the real reports; skipped if absent."""

    CLINGEN = os.path.join(REPORTS_DIR, "validation_report_clingen_real_v1.json")
    CLINVAR = os.path.join(REPORTS_DIR, "validation_report_clinvar_real_v1.json")
    CLINGEN_FIX = os.path.join(MODEL_DIR, "validation", "fixtures",
                               "clingen_real_v1.json")
    CLINVAR_FIX = os.path.join(MODEL_DIR, "validation", "fixtures",
                               "clinvar_real_v1.json")

    @unittest.skipUnless(
        os.path.exists(CLINGEN) and os.path.exists(CLINGEN_FIX),
        "clingen_real_v1 report/fixture not present")
    def test_clingen_has_four_serious(self):
        analysis = AF.analyze(AF._load_json(self.CLINGEN),
                              AF._load_json(self.CLINGEN_FIX),
                              benchmark="clingen_real_v1")
        self.assertEqual(analysis["totals"]["serious"], 4)

    @unittest.skipUnless(
        os.path.exists(CLINVAR) and os.path.exists(CLINVAR_FIX),
        "clinvar_real_v1 report/fixture not present")
    def test_clinvar_has_thirtyfour_serious(self):
        analysis = AF.analyze(AF._load_json(self.CLINVAR),
                              AF._load_json(self.CLINVAR_FIX),
                              benchmark="clinvar_real_v1")
        self.assertEqual(analysis["totals"]["serious"], 34)


if __name__ == "__main__":
    unittest.main()
