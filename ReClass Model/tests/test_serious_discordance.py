"""Unit tests for serious-discordance drill-down packets.

Run from ``ReClass Model/``:

    ../.venv/bin/python -m unittest tests.test_serious_discordance -v
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from validation import analyze_failures as AF
from validation import conflict_policy as CP
from engine import config as C


def _report_case(cid, expected, predicted, points, gene="GENE", group="Panel"):
    return {
        "id": cid,
        "gene": gene,
        "ancestry": group,
        "expected": expected,
        "predicted": predicted,
        "points": points,
        "match": expected == predicted,
        "serious": True,
    }


def _fixture_case(cid, expected, criteria=None, signals=None, enrichment=None):
    payload = {
        "id": cid,
        "gene": "GENE",
        "ancestry": "Panel",
        "expected": expected,
        "signals": signals or {"criteria": criteria or []},
    }
    if enrichment is not None:
        payload["enrichment"] = enrichment
    return payload


def _crit(name, direction, strength=None, *, points=None, source="curated", version="v1"):
    row = {"criterion": name, "direction": direction, "source": source, "version": version}
    if strength is not None:
        row["strength"] = strength
    if points is not None:
        row["points"] = points
    return row


class TestSeriousDiscordancePackets(unittest.TestCase):
    def test_unresolved_serious_discordance_blocks_release_until_disposition(self):
        report_case = _report_case("RB1", "Pathogenic", "Benign", -8.0)
        fixture_case = _fixture_case(
            "RB1",
            "Pathogenic",
            criteria=[_crit("BA1", "benign", "stand_alone")],
        )
        detail = AF.analyze(
            {"cases": [report_case], "metrics": {}, "benchmark": "tiny"},
            {"cases": [fixture_case]},
        )["serious_errors"][0]
        self.assertTrue(detail["release_blocking"])
        self.assertEqual(detail["root_cause_category"], detail["failure_cause"])
        self.assertIsNone(detail["reviewer_disposition"])

        fixture_case["adjudication"] = {"reviewer_disposition": "accepted_data_correction"}
        resolved = AF.analyze(
            {"cases": [report_case], "metrics": {}, "benchmark": "tiny"},
            {"cases": [fixture_case]},
        )["serious_errors"][0]
        self.assertFalse(resolved["release_blocking"])
        self.assertEqual(resolved["reviewer_disposition"], "accepted_data_correction")

    def test_conflict_policy_issue_includes_contribution_table(self):
        report_case = _report_case("C1", "Pathogenic", "Benign", 10.0)
        fixture_case = _fixture_case(
            "C1",
            "Pathogenic",
            criteria=[
                _crit("PVS1", "pathogenic", "very_strong", source="clingen", version="ERepo"),
                _crit("BA1", "benign", "stand_alone", source="clingen", version="ERepo"),
            ],
        )
        detail = AF.analyze(
            {"cases": [report_case], "metrics": {}, "benchmark": "tiny"},
            {"cases": [fixture_case]},
        )["serious_errors"][0]

        self.assertEqual(detail["failure_cause"], "conflict-policy issue")
        self.assertEqual(detail["candidate_type"], "human review")
        self.assertIn("BA1 stand-alone", " ".join(detail["classification_overrides"]))
        criteria = {row["criterion"]: row for row in detail["criteria_rows"]}
        self.assertEqual(criteria["PVS1"]["source_version"], "ERepo")
        contributions = {row["criterion"]: row for row in detail["point_contributions"]}
        self.assertEqual(contributions["PVS1"]["points"], 8.0)
        self.assertEqual(contributions["BA1"]["points"], -8.0)

    def test_evidence_absence_and_threshold_edge_are_counted(self):
        report = {
            "benchmark": "tiny",
            "metrics": {},
            "cases": [
                _report_case("E1", "Pathogenic", "Likely Benign", -2.0),
                _report_case("T1", "Pathogenic", "Likely Pathogenic", 9.5),
            ],
        }
        fixture = {
            "cases": [
                _fixture_case("E1", "Pathogenic", signals={"criteria": [], "revel": 0.05}),
                _fixture_case(
                    "T1",
                    "Pathogenic",
                    criteria=[_crit("PX", "pathogenic", points=9.5)],
                ),
            ]
        }
        analysis = AF.analyze(report, fixture, benchmark="tiny")
        by_id = {row["id"]: row for row in analysis["serious_errors"]}

        self.assertEqual(by_id["E1"]["failure_cause"], "evidence absence")
        self.assertEqual(by_id["E1"]["candidate_type"], "data")
        self.assertEqual(by_id["T1"]["failure_cause"], "threshold edge")
        self.assertEqual(by_id["T1"]["candidate_type"], "config proposal")

        causes = {
            row["failure_cause"]: row["count"]
            for row in analysis["rollups"]["serious_by_failure_cause"]
        }
        self.assertEqual(causes["evidence absence"], 1)
        self.assertEqual(causes["threshold edge"], 1)

    def test_reference_label_disagreement_takes_precedence(self):
        report_case = _report_case("L1", "Pathogenic", "Benign", -8.0)
        fixture_case = _fixture_case(
            "L1",
            "Pathogenic",
            criteria=[_crit("BA1", "benign", "stand_alone")],
            enrichment={"warnings": ["label_disagreement_with_clingen"]},
        )
        cause = AF.classify_failure_cause(report_case, fixture_case)
        self.assertEqual(cause, "reference-label disagreement")

    def test_markdown_has_required_drilldown_sections(self):
        report_case = _report_case("C1", "Pathogenic", "Benign", 0.0)
        fixture_case = _fixture_case(
            "C1",
            "Pathogenic",
            criteria=[_crit("BA1", "benign", "stand_alone")],
        )
        analysis = AF.analyze(
            {"cases": [report_case], "metrics": {}, "benchmark": "tiny"},
            {"cases": [fixture_case]},
            benchmark="tiny",
        )
        md = AF.render_markdown(analysis)
        self.assertIn("Supplied criteria and source versions", md)
        self.assertIn("Point-contribution table", md)
        self.assertIn("Root-cause classification", md)
        self.assertIn(AF.CLINICAL_RELEASE_STATE, md)


class TestConflictPolicy(unittest.TestCase):
    def _classification(self):
        return {
            "variant_key": "GRCh38-1-100-A-G",
            "contributions": [
                {
                    "source": "gnomad",
                    "acmg_criterion": "BA1",
                    "evidence_direction": "benign",
                    "applied_strength": "stand_alone",
                },
                {
                    "source": "clingen",
                    "acmg_criterion": "PVS1",
                    "evidence_direction": "pathogenic",
                    "applied_strength": "very_strong",
                },
            ],
        }

    def test_ba1_vs_curated_pathogenic_collision_is_flagged(self):
        result = CP.evaluate_conflict_policy(classification=self._classification())
        self.assertEqual(result["status"], "fail")
        self.assertEqual(len(result["violations"]), 1)
        self.assertEqual(result["violations"][0]["issue_code"], "BA1_CURATED_PATHOGENIC")

    def test_signed_variant_specific_exception_clears_collision(self):
        exception = {
            "exception_id": "ex-1",
            "variant_key": "GRCh38-1-100-A-G",
            "scope": "variant_specific",
            "conflict_codes": ["BA1_CURATED_PATHOGENIC"],
            "signed_off_by": "Dr. Reviewer",
            "signed_off_at": "2026-06-17T00:00:00+00:00",
        }
        result = CP.evaluate_conflict_policy(
            classification=self._classification(),
            exceptions=[exception],
        )
        self.assertEqual(result["status"], "pass")
        self.assertFalse(result["violations"])
        self.assertEqual(result["cleared_by_exceptions"][0]["exception_id"], "ex-1")

    def test_conflict_policy_does_not_mutate_global_thresholds(self):
        before = (C.BA1_AF, C.BS1_AF)
        result = CP.evaluate_conflict_policy(classification=self._classification())
        after = (C.BA1_AF, C.BS1_AF)
        self.assertEqual(after, before)
        self.assertFalse(result["global_threshold_mutated"])


if __name__ == "__main__":
    unittest.main()
