"""Tenant onboarding and admin API tests."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402

from api.app import create_app  # noqa: E402
from api.evidence_resolver import EvidenceResolver  # noqa: E402
from api.settings import Settings  # noqa: E402
from api.store import InMemoryClinicalStore  # noqa: E402
from ops.onboarding import preproduction_readiness_report  # noqa: E402
from storage.admin import InMemoryTenantAdminStore  # noqa: E402


class TestOnboardingReadiness(unittest.TestCase):
    def test_preproduction_report_passes_with_artifacts_and_oidc(self):
        with tempfile.TemporaryDirectory() as root:
            reference = os.path.join(root, "GRCh38.fa.meta.json")
            manifest = os.path.join(root, "revel.manifest.json")
            with open(reference, "w", encoding="utf-8") as fh:
                json.dump({
                    "build": "GRCh38",
                    "fasta_path": "/refs/GRCh38.fa",
                    "sha256": "abc",
                    "version": "release-110",
                }, fh)
            with open(manifest, "w", encoding="utf-8") as fh:
                json.dump({
                    "provider": "revel",
                    "source": "REVEL",
                    "source_version": "1.3",
                    "checksum": "def",
                }, fh)
            settings = Settings(
                environment="staging",
                auth_mode="oidc",
                oidc_issuer="https://idp.example/",
                oidc_jwks={"keys": [{"kid": "k1", "kty": "RSA"}]},
                reference_metadata_path=reference,
                provider_cache_manifest_path=manifest,
            )
            report = preproduction_readiness_report(
                settings,
                tenant={"tenant_id": str(uuid.uuid4()), "name": "Example Lab"},
                base_path=root,
            )
            self.assertTrue(report["ready"], report)
            self.assertEqual(report["preflight"]["status"], "ok")


class TestTenantAdminApi(unittest.TestCase):
    def test_create_update_and_readiness(self):
        tenant = str(uuid.uuid4())
        admin_store = InMemoryTenantAdminStore()
        app = create_app(
            settings=Settings(
                environment="development",
                legacy_default_roles=("viewer", "reviewer", "operator", "admin"),
            ),
            store=InMemoryClinicalStore(),
            resolver=EvidenceResolver(),
            admin_store=admin_store,
        )
        client = TestClient(app)
        headers = {"X-Tenant-Id": tenant}
        created = client.post(
            "/admin/tenants",
            json={
                "name": "Example Lab",
                "slug": "example-lab",
                "contact_email": "ops@example.test",
            },
            headers=headers,
        )
        self.assertEqual(created.status_code, 201, created.text)
        tenant_id = created.json()["tenant_id"]
        self.assertEqual(created.json()["status"], "onboarding")

        updated = client.patch(
            f"/admin/tenants/{tenant_id}",
            json={"status": "active"},
            headers=headers,
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()["status"], "active")

        listed = client.get("/admin/tenants", headers=headers)
        self.assertEqual(len(listed.json()), 1)

        readiness = client.get(f"/admin/tenants/{tenant_id}/readiness", headers=headers)
        self.assertEqual(readiness.status_code, 200)
        self.assertIn("checks", readiness.json())


if __name__ == "__main__":
    unittest.main()
