"""Alert triage workflow API tests."""

from __future__ import annotations

import os
import sys
import unittest
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402

from api.app import create_app  # noqa: E402
from api.evidence_resolver import EvidenceResolver  # noqa: E402
from api.settings import Settings  # noqa: E402
from api.store import InMemoryClinicalStore  # noqa: E402


class AlertTriageApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tenant = str(uuid.uuid4())
        app = create_app(
            settings=Settings(
                environment="development",
                legacy_default_roles=("viewer", "reviewer", "operator", "admin"),
            ),
            store=InMemoryClinicalStore(),
            resolver=EvidenceResolver(),
        )
        self.client = TestClient(app)

    def h(self):
        return {"X-Tenant-Id": self.tenant}

    @staticmethod
    def _ev(criterion, strength):
        return {
            "source": "curated",
            "acmg_criterion": criterion,
            "evidence_direction": "pathogenic",
            "applied_strength": strength,
        }

    def _create_alert(self):
        variant = {"chrom": "1", "pos": 5000, "ref": "A", "alt": "G"}
        self.client.post(
            "/reanalysis/run",
            json={"variant": variant, "evidence": {"events": [self._ev("PM2", "supporting")]}},
            headers=self.h(),
        )
        crossed = self.client.post(
            "/reanalysis/run",
            json={
                "variant": variant,
                "evidence": {"events": [
                    self._ev("PVS1", "very_strong"),
                    self._ev("PS1", "strong"),
                ]},
            },
            headers=self.h(),
        )
        return crossed.json()["result"]["alert_id"]

    def test_alert_triage_records_owner_sla_severity_resolution_and_notification(self):
        alert_id = self._create_alert()
        response = self.client.post(
            f"/alerts/{alert_id}/triage",
            json={
                "owner": "reviewer-a",
                "sla_due_at": "2026-06-20T00:00:00+00:00",
                "severity": "critical",
                "resolution_rationale": "requires amended report",
                "re_review_outcome": "amendment_required",
                "notification_state": "pending",
            },
            headers=self.h(),
        )
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(data["triage_owner"], "reviewer-a")
        self.assertEqual(data["severity"], "critical")
        self.assertEqual(data["re_review_outcome"], "amendment_required")
        self.assertEqual(data["notification_state"], "pending")
        self.assertIsNotNone(data["triaged_at"])

    def test_alert_triage_rejects_unknown_severity(self):
        alert_id = self._create_alert()
        response = self.client.post(
            f"/alerts/{alert_id}/triage",
            json={"severity": "catastrophic"},
            headers=self.h(),
        )
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
