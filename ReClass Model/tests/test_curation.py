"""Unit tests for curation-queue detection (job1 task 3).

Pure, offline. Verifies each curation gap is surfaced (and not over-surfaced) from a
resolved evidence bundle. Detection only *surfaces* gaps; resolution lives in Job 2.
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from evidence import curation as cur  # noqa: E402
from evidence.model import EvidenceBundle  # noqa: E402
from engine.scoring import EvidenceEvent  # noqa: E402


def _bundle(**kw):
    base = {"variant_key": "GRCh38-1-100-A-G", "events": [], "match": {}}
    base.update(kw)
    return base


class TestCurationItem(unittest.TestCase):
    def test_rejects_unknown_kind(self) -> None:
        with self.assertRaises(ValueError):
            cur.CurationItem(kind="not_a_kind")

    def test_default_detail_is_dict(self) -> None:
        item = cur.CurationItem(kind="missing_transcript", variant_key="GRCh38-1-1-A-G")
        self.assertEqual(item.detail, {})
        self.assertEqual(item.to_dict()["kind"], "missing_transcript")


class TestDetectors(unittest.TestCase):
    def test_unmatched_identity_when_nothing_resolved(self) -> None:
        b = _bundle(events=[], match={"revel": {"called": False, "status": "absent"}},
                    provider_versions={"revel": "REVEL_v1.3"})
        items = cur.detect_unmatched_identity(b)
        self.assertEqual([i.kind for i in items], ["unmatched_identity"])
        self.assertEqual(items[0].severity, "blocker")

    def test_no_unmatched_when_events_present(self) -> None:
        b = _bundle(events=[{"acmg_criterion": "PP3", "evidence_direction": "pathogenic"}],
                    match={"revel": {"called": True}})
        self.assertEqual(cur.detect_unmatched_identity(b), [])

    def test_no_unmatched_when_no_providers_configured(self) -> None:
        b = _bundle(events=[], match={}, warnings=["no_providers_configured"])
        self.assertEqual(cur.detect_unmatched_identity(b), [])

    def test_ambiguous_identity_from_candidates(self) -> None:
        b = _bundle(match={"clingen": {"candidates": [{"id": 1}, {"id": 2}]}})
        items = cur.detect_ambiguous_identity(b)
        self.assertEqual([i.kind for i in items], ["ambiguous_identity"])

    def test_ambiguous_identity_from_warning(self) -> None:
        b = _bundle(match={}, warnings=["clingen:ambiguous_match"])
        items = cur.detect_ambiguous_identity(b)
        self.assertEqual([i.kind for i in items], ["ambiguous_identity"])

    def test_missing_transcript_for_transcript_dependent_criterion(self) -> None:
        b = _bundle(events=[{"acmg_criterion": "PVS1", "evidence_direction": "pathogenic"}],
                    transcript=None)
        items = cur.detect_missing_transcript(b)
        self.assertEqual([i.kind for i in items], ["missing_transcript"])
        self.assertEqual(items[0].detail["criteria"], ["PVS1"])

    def test_no_missing_transcript_when_transcript_present(self) -> None:
        b = _bundle(events=[{"acmg_criterion": "PVS1", "evidence_direction": "pathogenic"}],
                    transcript={"mane_select": "NM_000277.3"})
        self.assertEqual(cur.detect_missing_transcript(b), [])

    def test_missing_cohort_denominator_for_ps4(self) -> None:
        b = _bundle(events=[{"acmg_criterion": "PS4", "evidence_direction": "pathogenic"}],
                    cohort_counts=None)
        items = cur.detect_missing_cohort_denominator(b)
        self.assertEqual([i.kind for i in items], ["missing_cohort_denominator"])

    def test_no_missing_denominator_when_known(self) -> None:
        b = _bundle(events=[{"acmg_criterion": "PS4", "evidence_direction": "pathogenic"}],
                    cohort_counts={"denominator": 200})
        self.assertEqual(cur.detect_missing_cohort_denominator(b), [])

    def test_pathogenic_benign_conflict(self) -> None:
        b = _bundle(events=[
            {"acmg_criterion": "PS3", "evidence_direction": "pathogenic"},
            {"acmg_criterion": "BS3", "evidence_direction": "benign"},
        ])
        items = cur.detect_pathogenic_benign_conflict(b)
        self.assertEqual([i.kind for i in items], ["pathogenic_benign_conflict"])
        self.assertEqual(items[0].detail["pathogenic"], ["PS3"])
        self.assertEqual(items[0].detail["benign"], ["BS3"])


class TestScanBundle(unittest.TestCase):
    def test_scan_accepts_evidence_bundle_object(self) -> None:
        bundle = EvidenceBundle(
            variant_key="GRCh38-1-100-A-G",
            events=[EvidenceEvent(source="reviewer", acmg_criterion="PVS1",
                                  evidence_direction="pathogenic", applied_strength="very_strong")],
        )
        items = cur.scan_bundle(bundle)
        kinds = [i.kind for i in items]
        self.assertIn("missing_transcript", kinds)

    def test_scan_clean_matched_bundle_has_no_items(self) -> None:
        bundle = {
            "variant_key": "GRCh38-1-100-A-G",
            "events": [{"acmg_criterion": "PP3", "evidence_direction": "pathogenic"}],
            "match": {"revel": {"called": True}},
            "transcript": {"mane_select": "NM_000277.3"},
            "cohort_counts": None,
        }
        self.assertEqual(cur.scan_bundle(bundle), [])

    def test_scan_is_deterministic_order(self) -> None:
        bundle = {
            "variant_key": "GRCh38-1-100-A-G",
            "events": [
                {"acmg_criterion": "PVS1", "evidence_direction": "pathogenic"},
                {"acmg_criterion": "BS3", "evidence_direction": "benign"},
            ],
            "match": {"clingen": {"called": True}},
            "transcript": None,
        }
        kinds = [i.kind for i in cur.scan_bundle(bundle)]
        # transcript detector runs before conflict detector.
        self.assertEqual(kinds, ["missing_transcript", "pathogenic_benign_conflict"])


if __name__ == "__main__":
    unittest.main()
