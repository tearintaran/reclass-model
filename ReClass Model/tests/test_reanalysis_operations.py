"""Operator views and tenant reanalysis policy tests."""

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
from monitoring.reanalysis import (  # noqa: E402
    operator_queue_view,
    operator_run_manifests,
    provider_cache_readiness,
    same_tier_changes,
)
from ops.scheduler import TenantReanalysisPolicy  # noqa: E402


class OperatorViewPureTests(unittest.TestCase):
    def test_operator_views_roll_up_queue_runs_cache_and_same_tier(self):
        queue = operator_queue_view([
            {"state": "pending"},
            {"state": "failed", "last_reason_code": "missing_provider_cache"},
        ])
        self.assertEqual(queue["by_state"]["pending"], 1)
        self.assertEqual(queue["reason_codes"]["missing_provider_cache"], 1)

        runs = operator_run_manifests([
            {
                "run_id": "r1",
                "trigger": "provider_version",
                "checked": 1,
                "same_tier": 1,
                "detail": [{"reason_code": "no_evidence"}],
            }
        ])
        self.assertEqual(runs[0]["reason_codes"]["no_evidence"], 1)

        readiness = provider_cache_readiness(
            [{"source": "revel", "version": "1.3", "sha256": "abc"}],
            required_sources=["revel", "gnomad"],
        )
        self.assertFalse(readiness["ready"])
        self.assertEqual(readiness["missing"], ["gnomad"])

        same = same_tier_changes([
            {"old_tier": "VUS", "new_tier": "VUS", "crossed": False},
            {"old_tier": "VUS", "new_tier": "Pathogenic", "crossed": True},
        ])
        self.assertEqual(len(same), 1)

    def test_tenant_policy_round_trip(self):
        policy = TenantReanalysisPolicy.from_dict({
            "cadence": "weekly",
            "included_sources": ["clinvar"],
            "affected_scope": {"genes": ["BRCA1"]},
            "retention": {"run_reports_days": 365},
        })
        data = policy.to_dict()
        self.assertEqual(data["cadence"], "weekly")
        self.assertEqual(data["included_sources"], ["clinvar"])
        self.assertEqual(data["affected_scope"]["genes"], ["BRCA1"])


class ReanalysisPolicyApiTests(unittest.TestCase):
    def test_policy_endpoint_persists_tenant_policy(self):
        tenant = str(uuid.uuid4())
        app = create_app(
            settings=Settings(
                environment="development",
                legacy_default_roles=("viewer", "reviewer", "operator", "admin"),
            ),
            store=InMemoryClinicalStore(),
            resolver=EvidenceResolver(),
        )
        client = TestClient(app)
        headers = {"X-Tenant-Id": tenant}
        response = client.post(
            "/reanalysis/policy",
            json={
                "cadence": "weekly",
                "included_sources": ["clinvar", "clingen"],
                "affected_scope": {"genes": ["BRCA1"]},
                "escalation_thresholds": {"serious_crossing": "critical"},
                "retention": {"run_reports_days": 365},
                "enabled": True,
            },
            headers=headers,
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["cadence"], "weekly")
        readback = client.get("/reanalysis/policy", headers=headers)
        self.assertEqual(readback.status_code, 200)
        self.assertEqual(readback.json()["included_sources"], ["clinvar", "clingen"])


if __name__ == "__main__":
    unittest.main()
