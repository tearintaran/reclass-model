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


class TestPlatformOperatorIsolation(unittest.TestCase):
    """Cross-tenant registry administration is platform-only (closes the C1/H1 chain)."""

    def _seed_app(self, settings):
        admin_store = InMemoryTenantAdminStore()
        t_a = admin_store.create_tenant(name="Lab A", slug="lab-a")
        t_b = admin_store.create_tenant(name="Lab B", slug="lab-b")
        app = create_app(
            settings=settings,
            store=InMemoryClinicalStore(),
            resolver=EvidenceResolver(),
            admin_store=admin_store,
        )
        return app, t_a["tenant_id"], t_b["tenant_id"]

    @staticmethod
    def _client_as(app, user):
        from api.deps import get_current_user
        app.dependency_overrides[get_current_user] = lambda: user
        return TestClient(app)

    def test_is_platform_operator_rules(self):
        from api.auth import UserContext
        from api.authz import is_platform_operator

        staging = Settings(environment="staging",
                           platform_admin_subjects=frozenset({"op-1"}))
        tenant_admin = UserContext("u-1", "t-1", frozenset({"admin"}))
        platform_op = UserContext("op-1", "t-x", frozenset({"admin"}))
        self.assertFalse(is_platform_operator(tenant_admin, staging))  # role alone insufficient
        self.assertTrue(is_platform_operator(platform_op, staging))    # allowlisted subject
        self.assertTrue(is_platform_operator(tenant_admin, Settings(environment="development")))

        # When bound to a platform issuer, a token from a tenant IdP is rejected.
        bound = Settings(environment="staging", platform_admin_subjects=frozenset({"op-1"}),
                         platform_oidc_issuer="https://platform.idp")
        self.assertFalse(is_platform_operator(
            UserContext("op-1", "t-x", frozenset({"admin"}), issuer="https://tenant.idp"), bound))
        self.assertTrue(is_platform_operator(
            UserContext("op-1", "t-x", frozenset({"admin"}), issuer="https://platform.idp"), bound))

    def test_tenant_admin_confined_to_own_tenant(self):
        from api.auth import UserContext
        settings = Settings(environment="staging",
                            platform_admin_subjects=frozenset({"op-1"}))
        app, t_a, t_b = self._seed_app(settings)
        client = self._client_as(app, UserContext("u-1", t_a, frozenset({"admin"})))

        # List is scoped to the caller's own tenant only.
        listed = client.get("/admin/tenants")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual([t["tenant_id"] for t in listed.json()], [t_a])

        self.assertEqual(client.get(f"/admin/tenants/{t_a}").status_code, 200)
        self.assertEqual(client.get(f"/admin/tenants/{t_b}").status_code, 404)  # existence hidden

        # The C1/H1 chain: a tenant admin cannot rewrite ANY tenant's OIDC binding,
        # not even its own, and cannot onboard tenants.
        self.assertEqual(
            client.patch(f"/admin/tenants/{t_b}", json={"oidc_issuer": "https://evil"}).status_code,
            403,
        )
        self.assertEqual(
            client.patch(f"/admin/tenants/{t_a}", json={"oidc_issuer": "https://evil"}).status_code,
            403,
        )
        self.assertEqual(
            client.post("/admin/tenants", json={"name": "X", "slug": "lab-x"}).status_code, 403
        )

    def test_platform_operator_has_full_registry_access(self):
        from api.auth import UserContext
        settings = Settings(environment="staging",
                            platform_admin_subjects=frozenset({"op-1"}))
        app, t_a, t_b = self._seed_app(settings)
        client = self._client_as(app, UserContext("op-1", t_a, frozenset({"admin"})))

        self.assertEqual(len(client.get("/admin/tenants").json()), 2)
        self.assertEqual(client.get(f"/admin/tenants/{t_b}").status_code, 200)
        self.assertEqual(
            client.patch(f"/admin/tenants/{t_b}", json={"status": "active"}).status_code, 200
        )
        self.assertEqual(
            client.post("/admin/tenants", json={"name": "Lab C", "slug": "lab-c"}).status_code, 201
        )


if __name__ == "__main__":
    unittest.main()
