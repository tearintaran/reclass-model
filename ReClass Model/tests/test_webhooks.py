"""Webhook registration, event emission, signing, retry, and API tests."""

from __future__ import annotations

import os
import sys
import unittest
import uuid
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402

from api.app import create_app  # noqa: E402
from api.evidence_resolver import EvidenceResolver  # noqa: E402
from api.settings import Settings  # noqa: E402
from api.store import InMemoryClinicalStore  # noqa: E402
from api.webhooks import deliver_due, emit_event, verify_signature  # noqa: E402
from storage.webhooks import InMemoryWebhookStore  # noqa: E402


class TestWebhookDelivery(unittest.TestCase):
    def setUp(self) -> None:
        self.tenant = str(uuid.uuid4())
        self.store = InMemoryWebhookStore()
        self.endpoint = self.store.register_endpoint(
            tenant_id=self.tenant,
            url="https://example.test/reclass",
            secret="x" * 32,
            event_types=["tier_crossing", "reanalysis_completed"],
        )

    def test_emit_creates_signed_delivery_and_marks_delivered(self):
        emit_event(
            self.store,
            tenant_id=self.tenant,
            event_type="tier_crossing",
            payload={"old_tier": "VUS", "new_tier": "Pathogenic"},
            source_id="alert-1",
        )
        captured = {}

        def sender(url, headers, body):
            captured.update({"url": url, "headers": headers, "body": body})
            return 204, ""

        result = deliver_due(self.store, sender=sender)
        self.assertEqual(result, {"delivered": 1, "retrying": 0, "failed": 0})
        self.assertEqual(captured["url"], "https://example.test/reclass")
        self.assertTrue(
            verify_signature("x" * 32, captured["body"], captured["headers"]["X-ReClass-Signature"])
        )
        deliveries = self.store.list_deliveries(tenant_id=self.tenant)
        self.assertEqual(deliveries[0]["state"], "delivered")
        self.assertEqual(deliveries[0]["attempts"], 1)

    def test_failed_delivery_retries_then_fails(self):
        emit_event(
            self.store,
            tenant_id=self.tenant,
            event_type="reanalysis_completed",
            payload={"run_id": "run-1"},
        )

        def sender(_url, _headers, _body):
            return 503, "try later"

        clock = datetime(2099, 1, 1, tzinfo=timezone.utc)
        first = deliver_due(self.store, sender=sender, now=clock, max_attempts=2)
        self.assertEqual(first["retrying"], 1)
        retry = self.store.list_deliveries(tenant_id=self.tenant)[0]
        self.assertEqual(retry["state"], "retry")
        self.assertIsNotNone(retry["next_attempt_at"])

        second = deliver_due(self.store, sender=sender, limit=10, max_attempts=2)
        self.assertEqual(second["failed"], 0, "delivery is not due before its retry time")


class TestWebhookApi(unittest.TestCase):
    def test_register_emit_and_list_delivery(self):
        tenant = str(uuid.uuid4())
        webhook_store = InMemoryWebhookStore()
        app = create_app(
            settings=Settings(
                environment="development",
                legacy_default_roles=("viewer", "reviewer", "operator", "admin"),
            ),
            store=InMemoryClinicalStore(),
            resolver=EvidenceResolver(),
            webhook_store=webhook_store,
        )
        client = TestClient(app)
        headers = {"X-Tenant-Id": tenant}
        created = client.post(
            "/webhooks/endpoints",
            json={
                "url": "https://customer.test/hook",
                "secret": "super-secret-signing-key",
                "event_types": ["config_change"],
            },
            headers=headers,
        )
        self.assertEqual(created.status_code, 201, created.text)
        self.assertEqual(created.json()["secret"], "***")

        emitted = client.post(
            "/webhooks/events",
            json={"event_type": "config_change", "payload": {"config": "base_v1"}},
            headers=headers,
        )
        self.assertEqual(emitted.status_code, 202, emitted.text)

        deliveries = client.get("/webhooks/deliveries", headers=headers)
        self.assertEqual(deliveries.status_code, 200)
        self.assertEqual(len(deliveries.json()), 1)
        self.assertEqual(deliveries.json()[0]["state"], "pending")

    def test_reanalysis_run_emits_lifecycle_events(self):
        tenant = str(uuid.uuid4())
        webhook_store = InMemoryWebhookStore()
        app = create_app(
            settings=Settings(
                environment="development",
                legacy_default_roles=("viewer", "reviewer", "operator", "admin"),
            ),
            store=InMemoryClinicalStore(),
            resolver=EvidenceResolver(),
            webhook_store=webhook_store,
        )
        client = TestClient(app)
        headers = {"X-Tenant-Id": tenant}
        client.post(
            "/webhooks/endpoints",
            json={
                "url": "https://customer.test/hook",
                "secret": "super-secret-signing-key",
                "event_types": ["reanalysis_completed", "tier_crossing"],
            },
            headers=headers,
        )

        first = client.post(
            "/reanalysis/run",
            json={
                "variant": {"chrom": "1", "pos": 500, "ref": "A", "alt": "G"},
                "evidence": {"events": [
                    {"source": "curated", "acmg_criterion": "PM2",
                     "evidence_direction": "pathogenic", "applied_strength": "supporting"}
                ]},
            },
            headers=headers,
        )
        self.assertEqual(first.status_code, 200, first.text)

        crossing = client.post(
            "/reanalysis/run",
            json={
                "variant": {"chrom": "1", "pos": 500, "ref": "A", "alt": "G"},
                "evidence": {"events": [
                    {"source": "curated", "acmg_criterion": "PVS1",
                     "evidence_direction": "pathogenic", "applied_strength": "very_strong"},
                    {"source": "curated", "acmg_criterion": "PS1",
                     "evidence_direction": "pathogenic", "applied_strength": "strong"},
                ]},
            },
            headers=headers,
        )
        self.assertEqual(crossing.status_code, 200, crossing.text)
        self.assertTrue(crossing.json()["result"]["crossed"])

        deliveries = client.get("/webhooks/deliveries", headers=headers).json()
        event_types = [d["event_type"] for d in deliveries]
        self.assertEqual(event_types.count("reanalysis_completed"), 2)
        self.assertEqual(event_types.count("tier_crossing"), 1)
        tier_delivery = next(d for d in deliveries if d["event_type"] == "tier_crossing")
        self.assertEqual(tier_delivery["payload"]["payload"]["new_tier"], "Pathogenic")
        self.assertTrue(tier_delivery["payload"]["payload"]["alert_id"])


if __name__ == "__main__":
    unittest.main()
