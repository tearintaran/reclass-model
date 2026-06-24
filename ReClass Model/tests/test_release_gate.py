"""Release-gate state and sign-off packet tests."""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from validation.release_gate import evaluate_release_gate  # noqa: E402
from validation.signoff import (  # noqa: E402
    APPROVED_FOR_RELEASE,
    RELEASED,
    REVIEW_PENDING,
    transition_release_state,
)


def _classification(**extra):
    data = {
        "classification_id": "c1",
        "variant_key": "GRCh38-1-100-A-G",
        "gene": "BRCA1",
        "disease": "hereditary breast and ovarian cancer",
        "tier": "Likely Pathogenic",
        "contributions": [
            {"evidence_direction": "pathogenic", "source": "curated"},
        ],
    }
    data.update(extra)
    return data


def _packet(**extra):
    data = {
        "signed_off_by": "Dr. Reviewer",
        "clinical_scope": {
            "active": True,
            "variant_keys": ["GRCh38-1-100-A-G"],
            "genes": ["BRCA1"],
            "diseases": ["hereditary breast and ovarian cancer"],
            "evidence_classes": ["pathogenic"],
        },
        "config_hash": "cfg1",
        "commit": "abc123",
        "source_snapshots": {"clinvar": "sha256:one"},
        "validation_report_id": "analytical-validation-test",
        "conflict_policy_disposition": "resolved",
        "reviewer_credential": "MD",
        "institutional_authorization": "lab-director-approval",
        "effective_date": "2026-06-19",
        "re_review_date": "2027-06-19",
        "second_reviewer": "Dr. Second",
    }
    data.update(extra)
    return data


class ReleaseGateTests(unittest.TestCase):
    def test_complete_packet_passes_and_moves_to_approved(self):
        result = evaluate_release_gate(
            classification=_classification(),
            signoff_packet=_packet(),
            active_config_hash="cfg1",
        )
        self.assertTrue(result.passed, result.to_dict())
        self.assertEqual(result.next_state, APPROVED_FOR_RELEASE)

    def test_missing_required_fields_block(self):
        result = evaluate_release_gate(
            classification=_classification(),
            signoff_packet={},
        )
        self.assertFalse(result.passed)
        codes = {issue.code for issue in result.blockers}
        self.assertIn("missing_signoff_fields", codes)
        self.assertIn("out_of_scope", codes)

    def test_out_of_scope_variant_blocks(self):
        packet = _packet(clinical_scope={"active": True, "genes": ["TP53"]})
        result = evaluate_release_gate(
            classification=_classification(),
            signoff_packet=packet,
        )
        self.assertFalse(result.passed)
        self.assertIn("out_of_scope", {issue.code for issue in result.blockers})

    def test_preflight_and_serious_discordance_block(self):
        result = evaluate_release_gate(
            classification=_classification(),
            signoff_packet=_packet(),
            preflight_failures=[{"name": "provider_cache_manifest", "message": "missing"}],
            serious_discordances=[{
                "variant_key": "GRCh38-1-100-A-G",
                "serious": True,
                "release_blocking": True,
            }],
        )
        codes = {issue.code for issue in result.blockers}
        self.assertIn("preflight_failed", codes)
        self.assertIn("unresolved_serious_discordance", codes)

    def test_release_state_machine(self):
        self.assertEqual(
            transition_release_state(REVIEW_PENDING, APPROVED_FOR_RELEASE),
            APPROVED_FOR_RELEASE,
        )
        self.assertEqual(transition_release_state(APPROVED_FOR_RELEASE, RELEASED), RELEASED)
        with self.assertRaises(ValueError):
            transition_release_state(RELEASED, APPROVED_FOR_RELEASE)


if __name__ == "__main__":
    unittest.main()
