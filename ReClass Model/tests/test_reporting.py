"""Tests for the reviewer-workflow reporting layer.

Pure unit tests (no DB, no API): they feed receipt/evidence/history dicts to the
report builders and assert the acceptance criteria — a reviewer can audit why a
tier was produced before sign-off; reports carry limitations, source versions,
warnings, and provenance; drafts stay drafts; same-tier changes appear as audit
history without high-priority alerting; and generated reports contain no
treatment directives.
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.scoring import EvidenceEvent, classify  # noqa: E402
from evidence.model import EvidenceBundle  # noqa: E402
from reporting import (  # noqa: E402
    build_patient_summary,
    build_reviewer_report,
    render_patient_summary_markdown,
    render_reviewer_markdown,
)

# Language that would make a report a clinical/treatment directive rather than
# decision support. Generated reports must avoid all of these.
_FORBIDDEN_DIRECTIVE_TERMS = [
    "treatment", "therapy", "medication", "prescrib", "dosage",
    "surgery", "we recommend", "you should take",
]


def _receipt(signed_off_by=None):
    events = [
        EvidenceEvent(source="revel", acmg_criterion="PP3",
                      evidence_direction="pathogenic", applied_strength="strong",
                      source_version="REVEL", raw={"revel_score": 0.95}),
        EvidenceEvent(source="gnomad", acmg_criterion="PM2",
                      evidence_direction="pathogenic", applied_strength="supporting",
                      source_version="gnomAD", raw={"popmax_af": 0.0}),
    ]
    clf = classify(events)
    bundle = EvidenceBundle(
        variant_key="GRCh38-1-100-A-G",
        events=events,
        provider_versions={"revel": "REVEL_v1.3", "gnomad": "gnomad_r4"},
        source_records=[{"source": "REVEL", "revel_score": 0.95}],
        warnings=["gnomad:gnomad_absent"],
        match={"revel": {"revel_match": True}},
    )
    receipt = {
        "classification_id": "c1",
        "tenant_id": "t1",
        "variant_id": "GRCh38-1-100-A-G",
        "variant_key": "GRCh38-1-100-A-G",
        "tier": clf.tier,
        "total_points": clf.total_points,
        "engine_version": clf.engine_version,
        "reconstruction_hash": clf.reconstruction_hash,
        "contributions": [c.__dict__ for c in clf.contributions],
        "overrides": list(clf.overrides),
        "signed_off_by": signed_off_by,
        "signed_off_at": "2026-01-01T00:00:00+00:00" if signed_off_by else None,
    }
    return receipt, bundle


class TestReviewerReport(unittest.TestCase):
    def test_auditable_before_sign_off(self):
        receipt, bundle = _receipt()
        report = build_reviewer_report(classification=receipt, evidence_bundle=bundle)
        self.assertTrue(report["release_status"]["is_draft"])
        # Every contribution is present with provenance + source version.
        criteria = {row["criterion"]: row for row in report["criteria"]}
        self.assertIn("PP3", criteria)
        self.assertEqual(criteria["PP3"]["source_version"], "REVEL")
        self.assertEqual(criteria["PP3"]["provenance"], {"revel_score": 0.95})
        # Evidence is grouped by source.
        self.assertIn("revel", report["evidence_by_source"])
        self.assertIn("gnomad", report["evidence_by_source"])

    def test_includes_limitations_versions_warnings_provenance(self):
        receipt, bundle = _receipt()
        report = build_reviewer_report(classification=receipt, evidence_bundle=bundle)
        self.assertTrue(report["limitations"])
        self.assertEqual(report["source_versions"]["revel"], "REVEL_v1.3")
        self.assertIn("gnomad:gnomad_absent", report["warnings"])
        self.assertEqual(report["evidence_provenance"]["source_records"],
                         [{"source": "REVEL", "revel_score": 0.95}])

    def test_reconstruction_hash_present(self):
        receipt, bundle = _receipt()
        report = build_reviewer_report(classification=receipt, evidence_bundle=bundle)
        self.assertEqual(report["classification"]["reconstruction_hash"],
                         receipt["reconstruction_hash"])

    def test_same_tier_changes_are_audit_not_alert(self):
        receipt, bundle = _receipt()
        reanalysis_events = [
            {"crossed": False, "old_tier": "Likely Pathogenic",
             "new_tier": "Likely Pathogenic", "alert_id": None},
            {"crossed": True, "old_tier": "VUS",
             "new_tier": "Likely Pathogenic", "alert_id": "a1"},
        ]
        alerts = [{"alert_id": "a1", "old_tier": "VUS", "new_tier": "Likely Pathogenic"}]
        report = build_reviewer_report(
            classification=receipt, evidence_bundle=bundle,
            reanalysis_events=reanalysis_events, alerts=alerts,
        )
        self.assertEqual(len(report["audit"]["same_tier_changes"]), 1)
        self.assertEqual(len(report["audit"]["tier_crossings"]), 1)
        self.assertEqual(len(report["history"]["alerts"]), 1)

    def test_previous_classifications_excludes_self(self):
        receipt, bundle = _receipt()
        prior = [receipt, {"classification_id": "c0", "tier": "VUS"}]
        report = build_reviewer_report(
            classification=receipt, evidence_bundle=bundle, prior_classifications=prior
        )
        ids = [p.get("classification_id") for p in report["history"]["previous_classifications"]]
        self.assertNotIn("c1", ids)
        self.assertIn("c0", ids)

    def test_markdown_renders_and_has_no_directives(self):
        receipt, bundle = _receipt()
        report = build_reviewer_report(classification=receipt, evidence_bundle=bundle)
        md = render_reviewer_markdown(report).lower()
        self.assertIn("technical reviewer report", md)
        for term in _FORBIDDEN_DIRECTIVE_TERMS:
            self.assertNotIn(term, md)

    def test_report_without_bundle_still_audits_contributions(self):
        receipt, _ = _receipt()
        report = build_reviewer_report(classification=receipt)
        self.assertEqual(len(report["criteria"]), 2)


class TestPatientSummary(unittest.TestCase):
    def test_draft_summary_marked_draft(self):
        receipt, _ = _receipt()
        report = build_patient_summary(classification=receipt)
        self.assertTrue(report["release_status"]["is_draft"])
        self.assertIn("draft", report["review_status"].lower())

    def test_signed_summary_marked_released(self):
        receipt, _ = _receipt(signed_off_by="Dr. Reviewer, MD")
        report = build_patient_summary(classification=receipt)
        self.assertFalse(report["release_status"]["is_draft"])
        self.assertIn("signed off", report["review_status"].lower())

    def test_includes_plain_language_and_limitations(self):
        receipt, _ = _receipt()
        report = build_patient_summary(classification=receipt)
        self.assertTrue(report["result"]["plain_language"])
        self.assertTrue(report["limitations"])

    def test_markdown_has_no_directives(self):
        receipt, _ = _receipt(signed_off_by="Dr. Reviewer, MD")
        report = build_patient_summary(classification=receipt)
        md = render_patient_summary_markdown(report).lower()
        self.assertIn("variant classification summary", md)
        for term in _FORBIDDEN_DIRECTIVE_TERMS:
            self.assertNotIn(term, md)


if __name__ == "__main__":
    unittest.main()
