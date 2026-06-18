"""Tests for operational audit logging."""

from __future__ import annotations

import os
import sys
import unittest
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402

from api.app import create_app  # noqa: E402
from api.audit import InMemoryAuditLog  # noqa: E402
from api.evidence_resolver import EvidenceResolver  # noqa: E402
from api.settings import Settings  # noqa: E402
from api.store import InMemoryClinicalStore  # noqa: E402
from evidence.gnomad import GnomadProvider  # noqa: E402
from evidence.revel import RevelProvider  # noqa: E402


def _resolver() -> EvidenceResolver:
    resolver = EvidenceResolver()
    resolver.register("revel", RevelProvider.from_scores({"1-100-A-G": 0.95}))
    resolver.register("gnomad", GnomadProvider.offline(path="/nonexistent/cache.json"))
    return resolver


class TestAuditLog(unittest.TestCase):
    def setUp(self) -> None:
        self.store = InMemoryClinicalStore()
        self.audit = InMemoryAuditLog(max_entries=100)
        self.tenant = str(uuid.uuid4())
        settings = Settings(
            environment="development",
            legacy_default_roles=("reviewer", "operator", "admin"),
        )
        app = create_app(
            settings=settings,
            store=self.store,
            resolver=_resolver(),
            audit_log=self.audit,
        )
        self.client = TestClient(app)

    def h(self):
        return {"X-Tenant-Id": self.tenant}

    def test_sign_off_creates_audit_entry(self):
        body = {
            "variant": {"chrom": "1", "pos": 100, "ref": "A", "alt": "G"},
            "evidence": {"events": [
                {"source": "curated", "acmg_criterion": "PVS1",
                 "evidence_direction": "pathogenic", "applied_strength": "very_strong"},
            ]},
        }
        cid = self.client.post("/classifications", json=body, headers=self.h()).json()
        cid = cid["receipt"]["classification_id"]
        self.client.post(f"/classifications/{cid}/sign-off",
                         json={"signed_off_by": "Dr. Audit, MD"}, headers=self.h())
        entries = self.client.get("/audit", headers=self.h()).json()
        actions = [e["action"] for e in entries]
        self.assertIn("classification.create", actions)
        self.assertIn("classification.sign_off", actions)

    def test_alert_state_change_audited(self):
        ev = {"source": "curated", "acmg_criterion": "PM2",
              "evidence_direction": "pathogenic", "applied_strength": "supporting"}
        var = {"variant": {"chrom": "1", "pos": 500, "ref": "A", "alt": "G"},
               "evidence": {"events": [ev]}}
        self.client.post("/reanalysis/run", json=var, headers=self.h())
        crossing = {"events": [
            ev,
            {"source": "curated", "acmg_criterion": "PVS1",
             "evidence_direction": "pathogenic", "applied_strength": "very_strong"},
            {"source": "curated", "acmg_criterion": "PS1",
             "evidence_direction": "pathogenic", "applied_strength": "strong"},
        ]}
        var["evidence"] = crossing
        self.client.post("/reanalysis/run", json=var, headers=self.h())
        alert_id = self.client.get("/alerts", headers=self.h()).json()[0]["alert_id"]
        self.client.post(f"/alerts/{alert_id}/state",
                         json={"state": "acknowledged"}, headers=self.h())
        entries = self.client.get("/audit?action=alert.state_change", headers=self.h()).json()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["resource_id"], alert_id)

    def test_audit_retention_prunes_oldest(self):
        log = InMemoryAuditLog(max_entries=3)
        for i in range(5):
            log.append(
                tenant_id=self.tenant,
                actor_id="x",
                action="test",
                resource_type="t",
                resource_id=str(i),
            )
        listed = log.list_entries(tenant_id=self.tenant, limit=10)
        self.assertEqual(len(listed), 3)
        ids = {e["resource_id"] for e in listed}
        self.assertNotIn("0", ids)
        self.assertIn("4", ids)


if __name__ == "__main__":
    unittest.main()
