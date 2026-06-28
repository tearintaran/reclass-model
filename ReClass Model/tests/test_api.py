"""Tests for the tenant-aware API.

These run WITHOUT a live database: each test builds an app with an injected
:class:`api.store.InMemoryClinicalStore` and an :class:`api.evidence_resolver`
populated with real providers used offline (REVEL from in-memory scores, gnomAD
from an empty cache) so evidence match / absence / failure are all exercised
deterministically. The suite covers valid input, missing evidence, invalid
variant identity, provider absence/failure, persistence + draft/sign-off,
reanalysis (churn guard, crossing alert, same-tier audit), alert-state lifecycle,
tenant isolation, the dev-only validation endpoint, and reviewer/summary reports.
"""

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
from evidence.gnomad import GnomadProvider  # noqa: E402
from evidence.revel import RevelProvider  # noqa: E402
from reclass_version import __version__ as SERVICE_VERSION  # noqa: E402


def _resolver() -> EvidenceResolver:
    resolver = EvidenceResolver()
    # REVEL match for locus 1-100-A-G (high score -> PP3 strong); any other locus
    # is absent (no_revel_score).
    resolver.register("revel", RevelProvider.from_scores({"1-100-A-G": 0.95}))
    # gnomAD offline with an empty cache -> a cache miss is a deterministic
    # `gnomad_not_cached` failure (never a network call).
    resolver.register("gnomad", GnomadProvider.offline(path="/nonexistent/cache.json"))
    return resolver


class _ApiTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self.store = InMemoryClinicalStore()
        self.settings = Settings(
            environment="development",
            legacy_default_roles=("viewer", "reviewer", "operator", "admin"),
        )
        app = create_app(settings=self.settings, store=self.store, resolver=_resolver())
        self.client = TestClient(app)
        self.tenant_a = str(uuid.uuid4())
        self.tenant_b = str(uuid.uuid4())

    def h(self, tenant_id: str) -> dict:
        return {"X-Tenant-Id": tenant_id}


class TestClassifyPreview(_ApiTestBase):
    def test_health(self):
        r = self.client.get("/health")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "ok")
        self.assertEqual(r.json()["service_version"], SERVICE_VERSION)
        self.assertEqual(r.json()["engine_version"], "1.0.0")

    def test_classify_with_events_valid(self):
        body = {"evidence": {"events": [
            {"source": "curated", "acmg_criterion": "PVS1",
             "evidence_direction": "pathogenic", "applied_strength": "very_strong"},
        ]}}
        r = self.client.post("/classify", json=body, headers=self.h(self.tenant_a))
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data["classification"]["tier"], "Likely Pathogenic")
        self.assertTrue(data["reconstruction_hash"])
        self.assertEqual(data["engine_version"], "1.0.0")
        self.assertTrue(data["is_draft"])  # a preview is never a clinical release
        self.assertIsNone(data["signed_off_by"])

    def test_classify_with_signals(self):
        body = {"evidence": {"signals": {"gnomad_af": 0.20}}}
        r = self.client.post("/classify", json=body, headers=self.h(self.tenant_a))
        self.assertEqual(r.status_code, 200)
        # BA1 stand-alone benign rule fires for a 20% allele frequency.
        self.assertEqual(r.json()["classification"]["tier"], "Benign")

    def test_classify_missing_evidence_is_vus_with_warning(self):
        r = self.client.post("/classify", json={"evidence": {"events": []}},
                             headers=self.h(self.tenant_a))
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data["classification"]["tier"], "VUS")
        self.assertIn("no_evidence_provided", data["warnings"])


class TestEvidenceResolve(_ApiTestBase):
    def test_resolve_match(self):
        body = {"variant": {"chrom": "1", "pos": 100, "ref": "A", "alt": "G"},
                "providers": ["revel"]}
        r = self.client.post("/evidence/resolve", json=body, headers=self.h(self.tenant_a))
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(len(data["events"]), 1)
        self.assertEqual(data["events"][0]["acmg_criterion"], "PP3")
        self.assertIn("revel", data["provider_versions"])

    def test_resolve_absent(self):
        body = {"variant": {"chrom": "2", "pos": 200, "ref": "C", "alt": "T"},
                "providers": ["revel"]}
        r = self.client.post("/evidence/resolve", json=body, headers=self.h(self.tenant_a))
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data["events"], [])
        self.assertIn("revel:no_revel_score", data["warnings"])

    def test_resolve_provider_failure(self):
        body = {"variant": {"chrom": "1", "pos": 100, "ref": "A", "alt": "G"},
                "providers": ["gnomad"]}
        r = self.client.post("/evidence/resolve", json=body, headers=self.h(self.tenant_a))
        self.assertEqual(r.status_code, 200)
        # Offline cache miss is a deterministic, non-poisoning failure.
        self.assertIn("gnomad:gnomad_not_cached", r.json()["warnings"])

    def test_resolve_unknown_provider(self):
        body = {"variant": {"chrom": "1", "pos": 100, "ref": "A", "alt": "G"},
                "providers": ["does_not_exist"]}
        r = self.client.post("/evidence/resolve", json=body, headers=self.h(self.tenant_a))
        self.assertEqual(r.status_code, 200)
        self.assertIn("unknown_provider:does_not_exist", r.json()["warnings"])

    def test_invalid_variant_identity(self):
        r = self.client.post("/evidence/resolve", json={"variant": {}},
                             headers=self.h(self.tenant_a))
        self.assertEqual(r.status_code, 422)


class TestEvidenceProviders(_ApiTestBase):
    """GET /evidence/providers — the configured-provider catalog the reviewer UI
    uses to populate its provider panel before any resolve (no hardcoded list)."""

    def test_lists_configured_providers_with_versions(self):
        r = self.client.get("/evidence/providers", headers=self.h(self.tenant_a))
        self.assertEqual(r.status_code, 200)
        providers = r.json()["providers"]
        names = [p["name"] for p in providers]
        # The base fixture registers revel + gnomad; the list is sorted by name.
        self.assertEqual(names, sorted(names))
        self.assertEqual(set(names), {"gnomad", "revel"})
        by_name = {p["name"]: p["version"] for p in providers}
        self.assertTrue(by_name["revel"])  # each provider reports a source version
        self.assertTrue(by_name["gnomad"])

    def test_empty_resolver_returns_empty_catalog(self):
        # A backend with no providers configured still answers (graceful empty),
        # so the client never blocks on a missing list.
        app = create_app(settings=self.settings, store=self.store,
                         resolver=EvidenceResolver())
        client = TestClient(app)
        r = client.get("/evidence/providers", headers=self.h(self.tenant_a))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["providers"], [])


class TestTenancy(_ApiTestBase):
    def _persist(self, tenant_id):
        body = {
            "variant": {"chrom": "1", "pos": 100, "ref": "A", "alt": "G"},
            "evidence": {"events": [
                {"source": "curated", "acmg_criterion": "PVS1",
                 "evidence_direction": "pathogenic", "applied_strength": "very_strong"}]},
        }
        return self.client.post("/classifications", json=body, headers=self.h(tenant_id))

    def test_missing_tenant_header(self):
        r = self._persist_no_header()
        self.assertEqual(r.status_code, 401)

    def _persist_no_header(self):
        return self.client.post("/classifications", json={
            "variant": {"chrom": "1", "pos": 100, "ref": "A", "alt": "G"}})

    def test_invalid_tenant_id(self):
        r = self.client.post("/classifications",
                             json={"variant": {"chrom": "1", "pos": 100, "ref": "A", "alt": "G"}},
                             headers={"X-Tenant-Id": "not-a-uuid"})
        self.assertEqual(r.status_code, 400)

    def test_persist_creates_draft(self):
        r = self._persist(self.tenant_a)
        self.assertEqual(r.status_code, 201)
        data = r.json()
        self.assertTrue(data["receipt"]["is_draft"])
        self.assertIsNone(data["receipt"]["signed_off_by"])
        self.assertEqual(data["classification"]["tier"], "Likely Pathogenic")

    def test_tenant_isolation(self):
        cid = self._persist(self.tenant_a).json()["receipt"]["classification_id"]
        # Visible to its own tenant.
        own = self.client.get(f"/classifications/{cid}", headers=self.h(self.tenant_a))
        self.assertEqual(own.status_code, 200)
        # Invisible to another tenant.
        other = self.client.get(f"/classifications/{cid}", headers=self.h(self.tenant_b))
        self.assertEqual(other.status_code, 404)

    def test_sign_off_releases_draft(self):
        cid = self._persist(self.tenant_a).json()["receipt"]["classification_id"]
        r = self.client.post(f"/classifications/{cid}/sign-off",
                             json={"signed_off_by": "Dr. Reviewer, MD"},
                             headers=self.h(self.tenant_a))
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertFalse(data["is_draft"])
        self.assertEqual(data["signed_off_by"], "Dr. Reviewer, MD")
        self.assertIsNotNone(data["signed_off_at"])

    def test_sign_off_requires_signer(self):
        cid = self._persist(self.tenant_a).json()["receipt"]["classification_id"]
        r = self.client.post(f"/classifications/{cid}/sign-off",
                             json={"signed_off_by": ""}, headers=self.h(self.tenant_a))
        self.assertEqual(r.status_code, 422)

    def test_sign_off_not_found(self):
        r = self.client.post(f"/classifications/{uuid.uuid4()}/sign-off",
                             json={"signed_off_by": "x"}, headers=self.h(self.tenant_a))
        self.assertEqual(r.status_code, 404)

    def test_persist_requires_locus(self):
        r = self.client.post("/classifications",
                             json={"variant": {"variation_id": "12345"}},
                             headers=self.h(self.tenant_a))
        self.assertEqual(r.status_code, 422)


class TestReanalysisAndAlerts(_ApiTestBase):
    VAR = {"chrom": "1", "pos": 500, "ref": "A", "alt": "G"}

    def _run(self, events, tenant=None):
        tenant = tenant or self.tenant_a
        body = {"variant": self.VAR, "evidence": {"events": events}}
        return self.client.post("/reanalysis/run", json=body, headers=self.h(tenant))

    def _ev(self, criterion, direction, strength):
        return {"source": "curated", "acmg_criterion": criterion,
                "evidence_direction": direction, "applied_strength": strength}

    def test_first_run_changes_no_alert(self):
        r = self._run([self._ev("PM2", "pathogenic", "supporting")])
        res = r.json()["result"]
        self.assertTrue(res["changed"])
        self.assertFalse(res["crossed"])
        self.assertIsNone(res["alert_id"])

    def test_churn_guard_identical_rerun(self):
        ev = [self._ev("PM2", "pathogenic", "supporting")]
        self._run(ev)
        res = self._run(ev).json()["result"]
        self.assertFalse(res["changed"])  # identical reconstruction hash -> no write

    def test_tier_crossing_creates_alert(self):
        self._run([self._ev("PM2", "pathogenic", "supporting")])  # VUS (1 pt)
        res = self._run([self._ev("PVS1", "pathogenic", "very_strong"),
                         self._ev("PS1", "pathogenic", "strong")]).json()["result"]  # 12 -> Pathogenic
        self.assertTrue(res["crossed"])
        self.assertIsNotNone(res["alert_id"])
        alerts = self.client.get("/alerts", headers=self.h(self.tenant_a)).json()
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["new_tier"], "Pathogenic")

    def test_same_tier_change_audited_without_alert(self):
        self._run([self._ev("PVS1", "pathogenic", "very_strong")])  # 8 -> Likely Pathogenic
        res = self._run([self._ev("PVS1", "pathogenic", "very_strong"),
                         self._ev("PP3", "pathogenic", "supporting")]).json()["result"]  # 9 -> still LP
        self.assertTrue(res["changed"])
        self.assertFalse(res["crossed"])
        self.assertIsNone(res["alert_id"])
        self.assertEqual(len(self.client.get("/alerts", headers=self.h(self.tenant_a)).json()), 0)
        events = self.client.get("/classifications", headers=self.h(self.tenant_a)).json()
        self.assertEqual(len(events), 2)

    def test_alert_state_lifecycle(self):
        self._run([self._ev("PM2", "pathogenic", "supporting")])
        self._run([self._ev("PVS1", "pathogenic", "very_strong"),
                   self._ev("PS1", "pathogenic", "strong")])
        alert_id = self.client.get("/alerts", headers=self.h(self.tenant_a)).json()[0]["alert_id"]

        ok = self.client.post(f"/alerts/{alert_id}/state", json={"state": "acknowledged"},
                              headers=self.h(self.tenant_a))
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(ok.json()["state"], "acknowledged")

        resolved = self.client.post(f"/alerts/{alert_id}/state", json={"state": "resolved"},
                                    headers=self.h(self.tenant_a))
        self.assertEqual(resolved.status_code, 200)

        # Reopening a terminal alert is an illegal transition -> 409.
        illegal = self.client.post(f"/alerts/{alert_id}/state", json={"state": "open"},
                                   headers=self.h(self.tenant_a))
        self.assertEqual(illegal.status_code, 409)

    def test_alert_unknown_state(self):
        self._run([self._ev("PM2", "pathogenic", "supporting")])
        self._run([self._ev("PVS1", "pathogenic", "very_strong"),
                   self._ev("PS1", "pathogenic", "strong")])
        alert_id = self.client.get("/alerts", headers=self.h(self.tenant_a)).json()[0]["alert_id"]
        r = self.client.post(f"/alerts/{alert_id}/state", json={"state": "bogus"},
                             headers=self.h(self.tenant_a))
        self.assertEqual(r.status_code, 400)

    def test_alert_not_found(self):
        r = self.client.post(f"/alerts/{uuid.uuid4()}/state", json={"state": "acknowledged"},
                             headers=self.h(self.tenant_a))
        self.assertEqual(r.status_code, 404)

    def test_reanalysis_requires_locus(self):
        r = self.client.post("/reanalysis/run",
                             json={"variant": {"variation_id": "1"}, "evidence": {"events": []}},
                             headers=self.h(self.tenant_a))
        self.assertEqual(r.status_code, 422)


class TestValidationEndpoint(_ApiTestBase):
    def test_validation_run_development(self):
        r = self.client.post("/validation/run", json={"benchmark": "synthetic_v1"},
                             headers=self.h(self.tenant_a))
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertIn("gate_pass", data)
        self.assertIn("metrics", data)

    def test_validation_unknown_benchmark(self):
        r = self.client.post("/validation/run", json={"benchmark": "no_such_bench"},
                             headers=self.h(self.tenant_a))
        self.assertEqual(r.status_code, 404)

    def test_validation_blocked_in_production(self):
        app = create_app(
            settings=Settings(environment="production", jwt_secret="test-secret"),
            store=InMemoryClinicalStore(), resolver=_resolver(),
        )
        client = TestClient(app)
        r = client.post("/validation/run", json={"benchmark": "synthetic_v1"})
        self.assertEqual(r.status_code, 401)


class TestReportEndpoints(_ApiTestBase):
    def _persist(self):
        body = {
            "variant": {"chrom": "1", "pos": 100, "ref": "A", "alt": "G"},
            "evidence": {"events": [
                {"source": "curated", "acmg_criterion": "PVS1",
                 "evidence_direction": "pathogenic", "applied_strength": "very_strong"}]},
        }
        return self.client.post("/classifications", json=body,
                                headers=self.h(self.tenant_a)).json()["receipt"]["classification_id"]

    def test_reviewer_report_json(self):
        cid = self._persist()
        r = self.client.get(f"/classifications/{cid}/report/reviewer",
                            headers=self.h(self.tenant_a))
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data["report_type"], "technical_reviewer")
        self.assertTrue(data["release_status"]["is_draft"])
        self.assertTrue(data["criteria"])  # the PVS1 contribution is auditable

    def test_reviewer_report_markdown(self):
        cid = self._persist()
        r = self.client.get(f"/classifications/{cid}/report/reviewer?format=markdown",
                            headers=self.h(self.tenant_a))
        self.assertEqual(r.status_code, 200)
        self.assertIn("# Technical reviewer report", r.text)

    def test_patient_summary(self):
        cid = self._persist()
        r = self.client.get(f"/classifications/{cid}/report/summary",
                            headers=self.h(self.tenant_a))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["report_type"], "patient_summary")


if __name__ == "__main__":
    unittest.main()
