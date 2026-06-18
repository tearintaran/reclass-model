"""Tests for authentication and authorization."""

from __future__ import annotations

import os
import sys
import json
import tempfile
import unittest
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402

from api.app import create_app  # noqa: E402
from api.auth import issue_jwt  # noqa: E402
from api.evidence_resolver import EvidenceResolver  # noqa: E402
from api.settings import PreflightError, Settings, preflight_check, require_preflight  # noqa: E402
from api.store import InMemoryClinicalStore  # noqa: E402


class TestAuth(unittest.TestCase):
    def setUp(self) -> None:
        self.secret = "test-secret-key"
        self.tenant = str(uuid.uuid4())
        self.store = InMemoryClinicalStore()
        self.settings = Settings(
            environment="production",
            jwt_secret=self.secret,
            api_keys={
                "static-key-1": {
                    "tenant_id": self.tenant,
                    "roles": ["reviewer"],
                    "user_id": "svc-reviewer",
                }
            },
        )
        app = create_app(settings=self.settings, store=self.store, resolver=EvidenceResolver())
        self.client = TestClient(app)

    def _jwt(self, roles=None):
        return issue_jwt(
            user_id="dr-smith",
            tenant_id=self.tenant,
            roles=roles or ["reviewer"],
            secret=self.secret,
        )

    def test_production_rejects_missing_auth(self):
        r = self.client.post("/classifications", json={
            "variant": {"chrom": "1", "pos": 1, "ref": "A", "alt": "G"},
            "evidence": {"events": []},
        }, headers={"X-Tenant-Id": self.tenant})
        self.assertEqual(r.status_code, 401)

    def test_jwt_authenticates(self):
        token = self._jwt()
        r = self.client.post("/classify", json={"evidence": {"events": []}},
                             headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(r.status_code, 200)

    def test_api_key_authenticates(self):
        r = self.client.post("/classify", json={"evidence": {"events": []}},
                             headers={"Authorization": "Bearer static-key-1"})
        self.assertEqual(r.status_code, 200)

    def test_invalid_token_rejected(self):
        r = self.client.post("/classify", json={"evidence": {"events": []}},
                             headers={"Authorization": "Bearer not-valid"})
        self.assertEqual(r.status_code, 401)

    def test_tenant_mismatch_forbidden(self):
        other = str(uuid.uuid4())
        token = self._jwt()
        r = self.client.post("/classify", json={"evidence": {"events": []}},
                             headers={
                                 "Authorization": f"Bearer {token}",
                                 "X-Tenant-Id": other,
                             })
        self.assertEqual(r.status_code, 403)

    def test_viewer_cannot_sign_off(self):
        token = self._jwt(roles=["viewer"])
        cid = str(uuid.uuid4())
        r = self.client.post(
            f"/classifications/{cid}/sign-off",
            json={"signed_off_by": "Dr. X"},
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(r.status_code, 403)

    def test_reviewer_cannot_run_reanalysis(self):
        token = self._jwt(roles=["reviewer"])
        r = self.client.post("/reanalysis/run", json={
            "variant": {"chrom": "1", "pos": 1, "ref": "A", "alt": "G"},
            "evidence": {"events": []},
        }, headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(r.status_code, 403)

    def test_operator_can_run_reanalysis(self):
        token = self._jwt(roles=["operator"])
        r = self.client.post("/reanalysis/run", json={
            "variant": {"chrom": "1", "pos": 1, "ref": "A", "alt": "G"},
            "evidence": {"events": [
                {"source": "curated", "acmg_criterion": "PM2",
                 "evidence_direction": "pathogenic", "applied_strength": "supporting"},
            ]},
        }, headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(r.status_code, 200)

    def test_metrics_endpoint(self):
        r = self.client.get("/metrics")
        self.assertEqual(r.status_code, 200)
        self.assertIn("reclass_http_requests_total", r.text)


class TestPreflight(unittest.TestCase):
    def test_missing_prerequisites_have_named_failures(self):
        settings = Settings(
            environment="production",
            audit_backend="memory",
            reference_metadata_path="missing-reference.json",
            provider_cache_manifest_path="missing-providers",
        )
        failures = preflight_check(settings, environ={}, base_path="/tmp")
        names = {failure.name for failure in failures}
        self.assertIn("required_environment_variables", names)
        self.assertIn("oidc_jwks_configuration", names)
        self.assertIn("audit_backend", names)
        self.assertIn("database_role", names)
        self.assertIn("reference_fasta_metadata", names)
        self.assertIn("provider_cache_manifest", names)
        with self.assertRaises(PreflightError) as ctx:
            require_preflight(settings, environ={}, base_path="/tmp")
        self.assertIn("oidc_jwks_configuration", str(ctx.exception))

    def test_preflight_passes_with_required_artifacts(self):
        with tempfile.TemporaryDirectory() as root:
            reference = os.path.join(root, "GRCh38.fa.meta.json")
            provider = os.path.join(root, "provider.manifest.json")
            with open(reference, "w", encoding="utf-8") as fh:
                json.dump({
                    "build": "GRCh38",
                    "fasta_path": "/refs/GRCh38.fa",
                    "sha256": "abc123",
                    "version": "release-110",
                }, fh)
            with open(provider, "w", encoding="utf-8") as fh:
                json.dump({
                    "source": "gnomAD",
                    "version": "v4.1",
                    "sha256": "def456",
                }, fh)
            settings = Settings(
                environment="production",
                db_role="reclass_app",
                oidc_issuer="https://idp.example/",
                oidc_jwks={"keys": [{"kty": "RSA", "kid": "test"}]},
                audit_backend="db",
                reference_metadata_path=reference,
                provider_cache_manifest_path=provider,
            )
            env = {
                "RECLASS_API_ENV": "production",
                "RECLASS_DB": "reclass_prod",
                "RECLASS_DB_ROLE": "reclass_app",
            }
            self.assertEqual(preflight_check(settings, environ=env, base_path=root), ())


if __name__ == "__main__":
    unittest.main()
