"""Tests for API request-size and rate-limit middleware."""

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


class TestRateLimitAndRequestSize(unittest.TestCase):
    def test_rate_limit_returns_429(self):
        app = create_app(
            settings=Settings(environment="development", rate_limit_per_minute=2),
            store=InMemoryClinicalStore(),
            resolver=EvidenceResolver(),
        )
        client = TestClient(app)
        self.assertEqual(client.get("/health").status_code, 200)
        self.assertEqual(client.get("/health").status_code, 200)
        limited = client.get("/health")
        self.assertEqual(limited.status_code, 429)
        self.assertEqual(limited.headers["Retry-After"], "59")

    def test_request_size_limit_returns_413(self):
        tenant = str(uuid.uuid4())
        app = create_app(
            settings=Settings(
                environment="development",
                legacy_default_roles=("viewer", "reviewer", "operator", "admin"),
                request_size_limit_bytes=12,
            ),
            store=InMemoryClinicalStore(),
            resolver=EvidenceResolver(),
        )
        client = TestClient(app)
        response = client.post(
            "/classify",
            json={"evidence": {"events": []}},
            headers={"X-Tenant-Id": tenant},
        )
        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.json()["detail"], "request body too large")


if __name__ == "__main__":
    unittest.main()
