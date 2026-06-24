"""Production preflight default behavior tests."""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.settings import Settings, get_settings, preflight_check, readiness_report  # noqa: E402


class TestProductionPreflightDefaults(unittest.TestCase):
    def test_get_settings_production_defaults_fail_closed(self):
        with patch.dict(os.environ, {"RECLASS_API_ENV": "production"}, clear=True):
            settings = get_settings()
        self.assertEqual(settings.auth_mode, "oidc")
        self.assertTrue(settings.preflight_on_startup)
        self.assertTrue(settings.preflight_check_database)
        self.assertGreater(settings.rate_limit_per_minute, 0)
        self.assertGreater(settings.request_size_limit_bytes, 0)

    def test_preflight_names_auth_mode_failure(self):
        settings = Settings(
            environment="production",
            auth_mode="auto",
            audit_backend="db",
            db_role="reclass_app",
            oidc_issuer="https://idp.example",
            oidc_jwks={"keys": []},
            reference_metadata_path="missing-reference.json",
            provider_cache_manifest_path="missing-provider.json",
        )
        failures = preflight_check(settings, environ={
            "RECLASS_API_ENV": "production",
            "RECLASS_DB": "reclass_prod",
            "RECLASS_DB_ROLE": "reclass_app",
        }, base_path="/tmp")
        self.assertIn("production_auth_mode", {failure.name for failure in failures})

    def test_readiness_report_exposes_named_checks(self):
        report = readiness_report(Settings(), base_path="/tmp")
        self.assertIn("checks", report)
        self.assertIn("reference_metadata", report["checks"])
        self.assertIn("provider_cache_manifests", report["checks"])
        self.assertIn("artifacts", report)


if __name__ == "__main__":
    unittest.main()
