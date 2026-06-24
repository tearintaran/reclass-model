"""Exportable release validation packet tests."""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from validation.analytical_validation import validation_report_id  # noqa: E402
from validation.release_packet import build_release_validation_packet, packet_digest  # noqa: E402


class ReleasePacketTests(unittest.TestCase):
    def test_packet_bundles_required_release_governance_sections(self):
        packet = build_release_validation_packet(
            release_scope={"genes": ["BRCA1"]},
            config_hash="cfg1",
            source_snapshots={"clinvar": {"version": "2026-01"}},
            benchmark_metrics=[{"benchmark": "synthetic_v1", "metrics": {"n": 10}}],
            serious_discordances=[{"id": "d1", "resolved": True}],
            sign_off_ledger=[{"signed_off_by": "Dr. Reviewer"}],
            validation_report_id="analytical-validation-test",
        )
        self.assertEqual(packet["packet_type"], "scoped_release_validation_packet")
        self.assertEqual(packet["config_hash"], "cfg1")
        self.assertIn("clinvar", packet["source_snapshots"])
        self.assertEqual(packet["benchmark_metrics"][0]["benchmark"], "synthetic_v1")
        self.assertEqual(packet["serious_discordance_disposition"]["total"], 1)
        self.assertEqual(packet["sign_off_ledger"][0]["signed_off_by"], "Dr. Reviewer")
        self.assertTrue(packet["packet_id"].startswith("release-packet-"))

    def test_packet_digest_is_stable_against_generated_time(self):
        packet = build_release_validation_packet(
            release_scope={"genes": ["BRCA1"]},
            config_hash="cfg1",
            validation_report_id="r1",
        )
        changed_time = dict(packet)
        changed_time["generated_utc"] = "2030-01-01T00:00:00+00:00"
        self.assertEqual(packet_digest(packet), packet_digest(changed_time))

    def test_validation_report_id_is_stable_for_same_metrics(self):
        report = {
            "engine_version": "1.0.0",
            "config_hash": "cfg1",
            "benchmarks": [{"benchmark": "synthetic", "case_count": 1, "metrics": {"n": 1}}],
        }
        self.assertEqual(validation_report_id(report), validation_report_id(dict(report)))


if __name__ == "__main__":
    unittest.main()
