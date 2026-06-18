"""End-to-end API workflow contract test.

This locks down the product path most likely to regress when route, auth,
reporting, or in-memory workflow behavior changes:

resolve evidence -> classify preview -> persist draft -> reviewer report
-> sign-off -> patient summary -> reanalysis -> alert lifecycle.
"""

from __future__ import annotations

import os
import sys
import unittest
import uuid
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402

from api.app import create_app  # noqa: E402
from api.auth import issue_jwt  # noqa: E402
from api.evidence_resolver import EvidenceResolver  # noqa: E402
from api.openapi_contract import load_pinned_openapi, stable_json  # noqa: E402
from api.cookbook_examples import run_all as run_cookbook_examples  # noqa: E402
from api.settings import Settings  # noqa: E402
from api.store import InMemoryClinicalStore  # noqa: E402
from evidence.gnomad import GnomadProvider  # noqa: E402
from evidence.revel import RevelProvider  # noqa: E402


VARIANT = {"chrom": "1", "pos": 100, "ref": "A", "alt": "G"}
RESOLVE_EVIDENCE = {
    "resolve": {
        "variant": VARIANT,
        "providers": ["revel"],
    }
}
PATHOGENIC_REANALYSIS_EVIDENCE = {
    "events": [
        {
            "source": "curated",
            "acmg_criterion": "PVS1",
            "evidence_direction": "pathogenic",
            "applied_strength": "very_strong",
            "source_version": "manual-curation-v1",
        },
        {
            "source": "curated",
            "acmg_criterion": "PS1",
            "evidence_direction": "pathogenic",
            "applied_strength": "strong",
            "source_version": "manual-curation-v1",
        },
    ]
}


def _resolver() -> EvidenceResolver:
    resolver = EvidenceResolver()
    resolver.register("revel", RevelProvider.from_scores({"1-100-A-G": 0.95}))
    resolver.register("gnomad", GnomadProvider.offline(path="/nonexistent/cache.json"))
    return resolver


class TestEndToEndApiContract(unittest.TestCase):
    def setUp(self) -> None:
        self.secret = "api-contract-secret"
        self.tenant_a = str(uuid.uuid4())
        self.tenant_b = str(uuid.uuid4())
        settings = Settings(environment="production", jwt_secret=self.secret)
        app = create_app(
            settings=settings,
            store=InMemoryClinicalStore(),
            resolver=_resolver(),
        )
        self.client = TestClient(app)

    def auth(self, tenant_id: str, roles: list[str], user_id: str = "contract-user") -> dict:
        token = issue_jwt(
            user_id=user_id,
            tenant_id=tenant_id,
            roles=roles,
            secret=self.secret,
            display_name=user_id,
        )
        return {"Authorization": f"Bearer {token}"}

    def assert_status(self, stage: str, response, expected: int) -> None:
        self.assertEqual(response.status_code, expected, f"{stage}: {response.text}")

    def resolve_variant(self) -> dict:
        response = self.client.post(
            "/evidence/resolve",
            json={"variant": VARIANT, "providers": ["revel"]},
            headers=self.auth(self.tenant_a, ["viewer"], "viewer-a"),
        )
        self.assert_status("resolve evidence", response, 200)
        return response.json()

    def classify_preview(self, tenant_id: str = None) -> dict:
        response = self.client.post(
            "/classify",
            json={"variant": VARIANT, "evidence": RESOLVE_EVIDENCE},
            headers=self.auth(tenant_id or self.tenant_a, ["viewer"], "viewer"),
        )
        self.assert_status("classify preview", response, 200)
        return response.json()

    def persist_draft(self, tenant_id: str = None) -> dict:
        response = self.client.post(
            "/classifications",
            json={"variant": VARIANT, "evidence": RESOLVE_EVIDENCE},
            headers=self.auth(tenant_id or self.tenant_a, ["reviewer"], "reviewer"),
        )
        self.assert_status("persist draft", response, 201)
        return response.json()

    def get_reviewer_report(self, classification_id: str, tenant_id: str = None) -> dict:
        response = self.client.get(
            f"/classifications/{classification_id}/report/reviewer",
            headers=self.auth(tenant_id or self.tenant_a, ["viewer"], "viewer"),
        )
        self.assert_status("reviewer report", response, 200)
        return response.json()

    def sign_off(
        self,
        classification_id: str,
        *,
        tenant_id: str = None,
        roles: list[str] = None,
        expected: int = 200,
    ) -> dict:
        response = self.client.post(
            f"/classifications/{classification_id}/sign-off",
            json={"signed_off_by": "Dr. Contract Reviewer, MD", "credential": "MD"},
            headers=self.auth(
                tenant_id or self.tenant_a,
                roles or ["reviewer"],
                "signer",
            ),
        )
        self.assert_status("sign-off", response, expected)
        return response.json() if response.content else {}

    def get_patient_summary(self, classification_id: str, tenant_id: str = None) -> dict:
        response = self.client.get(
            f"/classifications/{classification_id}/report/summary",
            headers=self.auth(tenant_id or self.tenant_a, ["viewer"], "viewer"),
        )
        self.assert_status("patient summary", response, 200)
        return response.json()

    def run_reanalysis(self) -> dict:
        response = self.client.post(
            "/reanalysis/run",
            json={
                "variant": VARIANT,
                "evidence": PATHOGENIC_REANALYSIS_EVIDENCE,
                "trigger": "contract-test",
            },
            headers=self.auth(self.tenant_a, ["admin"], "operator-a"),
        )
        self.assert_status("reanalysis", response, 200)
        return response.json()

    def assert_tenant_isolation(self, tenant_a_classification_id: str) -> None:
        tenant_b_read = self.client.get(
            f"/classifications/{tenant_a_classification_id}",
            headers=self.auth(self.tenant_b, ["admin"], "admin-b"),
        )
        self.assert_status("tenant B read tenant A classification", tenant_b_read, 404)

        tenant_b_report = self.client.get(
            f"/classifications/{tenant_a_classification_id}/report/reviewer",
            headers=self.auth(self.tenant_b, ["admin"], "admin-b"),
        )
        self.assert_status("tenant B read tenant A report", tenant_b_report, 404)

        tenant_b_draft = self.persist_draft(self.tenant_b)
        tenant_b_classification_id = tenant_b_draft["receipt"]["classification_id"]
        tenant_a_read = self.client.get(
            f"/classifications/{tenant_b_classification_id}",
            headers=self.auth(self.tenant_a, ["admin"], "admin-a"),
        )
        self.assert_status("tenant A read tenant B classification", tenant_a_read, 404)

    def assert_alert_lifecycle(self, alert_id: str) -> None:
        tenant_a_alerts = self.client.get(
            "/alerts",
            headers=self.auth(self.tenant_a, ["admin"], "admin-a"),
        )
        self.assert_status("tenant A list alerts", tenant_a_alerts, 200)
        alerts = tenant_a_alerts.json()
        self.assertEqual(len(alerts), 1, "alert lifecycle: expected one tenant A alert")
        self.assertEqual(alerts[0]["alert_id"], alert_id)
        self.assertEqual(alerts[0]["state"], "open")
        self.assertEqual(alerts[0]["old_tier"], "VUS")
        self.assertEqual(alerts[0]["new_tier"], "Pathogenic")

        tenant_b_alerts = self.client.get(
            "/alerts",
            headers=self.auth(self.tenant_b, ["admin"], "admin-b"),
        )
        self.assert_status("tenant B list tenant A alerts", tenant_b_alerts, 200)
        self.assertEqual(tenant_b_alerts.json(), [])

        tenant_b_update = self.client.post(
            f"/alerts/{alert_id}/state",
            json={"state": "acknowledged"},
            headers=self.auth(self.tenant_b, ["admin"], "admin-b"),
        )
        self.assert_status("tenant B update tenant A alert", tenant_b_update, 404)

        acknowledged = self.client.post(
            f"/alerts/{alert_id}/state",
            json={"state": "acknowledged"},
            headers=self.auth(self.tenant_a, ["reviewer"], "reviewer-a"),
        )
        self.assert_status("acknowledge alert", acknowledged, 200)
        self.assertEqual(acknowledged.json()["state"], "acknowledged")

        resolved = self.client.post(
            f"/alerts/{alert_id}/state",
            json={"state": "resolved"},
            headers=self.auth(self.tenant_a, ["reviewer"], "reviewer-a"),
        )
        self.assert_status("resolve alert", resolved, 200)
        self.assertEqual(resolved.json()["state"], "resolved")
        self.assertIsNotNone(resolved.json()["resolved_at"])

    def test_api_workflow_contract(self) -> None:
        resolved = self.resolve_variant()
        self.assertEqual(resolved["provider_versions"]["revel"], "REVEL_v1.3")
        self.assertEqual(resolved["source_records"][0]["dataset"], "REVEL_v1.3")
        self.assertEqual(resolved["events"][0]["source"], "revel")
        self.assertEqual(resolved["events"][0]["source_version"], "REVEL")
        self.assertEqual(
            resolved["match"]["revel"]["canonical_key"],
            "GRCh38-1-100-A-G",
        )

        preview = self.classify_preview()
        self.assertTrue(preview["is_draft"])
        self.assertIsNone(preview["signed_off_by"])
        self.assertEqual(preview["classification"]["tier"], "VUS")
        self.assertEqual(preview["provider_versions"], resolved["provider_versions"])
        self.assertEqual(preview["evidence"]["source_records"], resolved["source_records"])
        self.assertTrue(preview["reconstruction_hash"])

        draft = self.persist_draft()
        receipt = draft["receipt"]
        classification_id = receipt["classification_id"]
        self.assertTrue(receipt["is_draft"])
        self.assertIsNone(receipt["signed_off_by"])
        self.assertEqual(draft["classification"]["tier"], preview["classification"]["tier"])
        self.assertEqual(draft["provider_versions"], resolved["provider_versions"])
        self.assertEqual(draft["reconstruction_hash"], preview["reconstruction_hash"])
        self.assertEqual(receipt["reconstruction_hash"], preview["reconstruction_hash"])

        contribution = receipt["contributions"][0]
        self.assertEqual(contribution["source"], resolved["events"][0]["source"])
        self.assertEqual(
            contribution["source_version"],
            resolved["events"][0]["source_version"],
        )
        self.assertEqual(
            contribution["acmg_criterion"],
            resolved["events"][0]["acmg_criterion"],
        )

        self.assert_tenant_isolation(classification_id)

        reviewer_report = self.get_reviewer_report(classification_id)
        self.assertTrue(reviewer_report["release_status"]["is_draft"])
        self.assertEqual(
            reviewer_report["release_status"]["status"],
            "DRAFT \u2014 not for clinical use",
        )
        self.assertEqual(
            reviewer_report["classification"]["classification_id"],
            classification_id,
        )
        self.assertEqual(
            reviewer_report["classification"]["tier"],
            receipt["tier"],
        )
        self.assertEqual(
            reviewer_report["classification"]["reconstruction_hash"],
            receipt["reconstruction_hash"],
        )
        self.assertEqual(
            reviewer_report["criteria"][0]["source_version"],
            resolved["events"][0]["source_version"],
        )

        self.sign_off(classification_id, roles=["viewer"], expected=403)
        signed = self.sign_off(classification_id, roles=["reviewer"])
        self.assertFalse(signed["is_draft"])
        self.assertEqual(signed["signed_off_by"], "Dr. Contract Reviewer, MD")
        self.assertEqual(signed["reconstruction_hash"], receipt["reconstruction_hash"])

        patient_summary = self.get_patient_summary(classification_id)
        self.assertFalse(patient_summary["release_status"]["is_draft"])
        self.assertEqual(patient_summary["result"]["classification"], receipt["tier"])
        self.assertEqual(
            patient_summary["identity"]["variant_key"],
            reviewer_report["identity"]["variant_key"],
        )
        self.assertEqual(
            patient_summary["release_status"]["signed_off_by"],
            signed["signed_off_by"],
        )

        reanalysis = self.run_reanalysis()
        result = reanalysis["result"]
        self.assertTrue(result["changed"])
        self.assertTrue(result["crossed"])
        self.assertEqual(result["old_tier"], "VUS")
        self.assertEqual(result["new_tier"], "Pathogenic")
        self.assertIsNotNone(result["new_classification_id"])
        self.assertIsNotNone(result["alert_id"])

        self.assert_alert_lifecycle(result["alert_id"])


class TestPinnedOpenAPI(unittest.TestCase):
    def test_live_schema_matches_pinned_artifact(self) -> None:
        app = create_app(
            settings=Settings(environment="development"),
            store=InMemoryClinicalStore(),
            resolver=_resolver(),
        )
        live = json.loads(stable_json(app.openapi()))
        self.assertEqual(live, load_pinned_openapi())


class TestCookbookExamples(unittest.TestCase):
    def test_all_cookbook_examples_run_against_test_app(self) -> None:
        tenant_id = str(uuid.uuid4())
        app = create_app(
            settings=Settings(
                environment="development",
                legacy_default_roles=("viewer", "reviewer", "operator", "admin"),
            ),
            store=InMemoryClinicalStore(),
            resolver=_resolver(),
        )
        client = TestClient(app)
        results = run_cookbook_examples(client, {"X-Tenant-Id": tenant_id})
        self.assertEqual(
            set(results),
            {"evidence_resolution", "classify", "sign_off", "report", "reanalysis", "alert"},
        )
        self.assertEqual(results["report"]["fhir"]["resourceType"], "Bundle")
        self.assertEqual(results["alert"]["state"], "acknowledged")


if __name__ == "__main__":
    unittest.main()
