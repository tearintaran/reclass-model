"""Cross-job integration: Job-1 evidence fields survive Job-3 transport.

Jobs 1/2/3 were built independently against their own fixtures; the PS4 cohort
counts (job1 task 5) and MANE Select transcript identity (job1 task 4) are the
fields that must cross the Job-1 -> Job-3 boundary. Job 1 attaches them to the
:class:`~evidence.model.EvidenceBundle` a provider returns; Job 3 must carry them
through the resolver merge and out the API surface.

These tests pin that wiring so it cannot silently regress to the earlier state
where the resolver merge dropped ``transcript`` / ``cohort_counts`` on the floor
and the ``/evidence/resolve`` response omitted them entirely. They use small
in-memory providers (no network, no fixtures) so the seam is exercised directly.

Run from ``ReClass Model/``::

    ../.venv/bin/python -m unittest tests.test_integration_wiring -v
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
from evidence.model import CohortCounts, EvidenceBundle, TranscriptIdentity  # noqa: E402
from evidence.providers import EvidenceProvider  # noqa: E402
from engine.scoring import EvidenceEvent  # noqa: E402


VARIANT = {"chrom": "1", "pos": 100, "ref": "A", "alt": "G"}
VARIANT_KEY = "1-100-A-G"


class _TranscriptProvider(EvidenceProvider):
    """A provider that attaches a MANE Select transcript identity to its bundle."""

    name = "tx"
    version = "tx-v1"

    def fetch(self, case_or_variant):  # noqa: D401 - see base class
        return EvidenceBundle(
            variant_key=VARIANT_KEY,
            provider_versions={"tx": self.version},
            transcript=TranscriptIdentity(
                mane_select="NM_007294.4", gene="BRCA1", hgvs_c="c.68_69del"
            ),
        )


class _CohortProvider(EvidenceProvider):
    """A provider that attaches PS4 cohort counts (and one PS4 event) to its bundle."""

    name = "cohort"
    version = "cc-v1"

    def fetch(self, case_or_variant):  # noqa: D401 - see base class
        return EvidenceBundle(
            variant_key=VARIANT_KEY,
            events=[
                EvidenceEvent(
                    source="cohort",
                    acmg_criterion="PS4",
                    evidence_direction="pathogenic",
                    applied_strength="strong",
                    source_version=self.version,
                )
            ],
            provider_versions={"cohort": self.version},
            cohort_counts=CohortCounts(
                case_count=40, case_total=100, control_count=5, control_total=100,
                odds_ratio=12.67, cohort="Cohort-A", source="curated_cc",
            ),
        )


class TestResolverPropagation(unittest.TestCase):
    """The merged bundle must carry transcript + cohort counts its providers set."""

    def test_single_provider_transcript_survives_merge(self):
        resolver = EvidenceResolver({"tx": _TranscriptProvider()})
        merged = resolver.resolve(VARIANT, variant_key=VARIANT_KEY)["bundle"]
        self.assertIsNotNone(merged.transcript)
        self.assertEqual(merged.transcript.mane_select, "NM_007294.4")

    def test_single_provider_cohort_counts_survive_merge(self):
        resolver = EvidenceResolver({"cohort": _CohortProvider()})
        merged = resolver.resolve(VARIANT, variant_key=VARIANT_KEY)["bundle"]
        self.assertIsNotNone(merged.cohort_counts)
        self.assertEqual(merged.cohort_counts.denominator, 200)
        self.assertEqual(merged.cohort_counts.case_count, 40)

    def test_fields_merge_independently_across_providers(self):
        # transcript comes from one provider, cohort counts from another: the merge
        # must keep both, not just whichever provider sorted first.
        resolver = EvidenceResolver(
            {"tx": _TranscriptProvider(), "cohort": _CohortProvider()}
        )
        merged = resolver.resolve(VARIANT, variant_key=VARIANT_KEY)["bundle"]
        self.assertEqual(merged.transcript.mane_select, "NM_007294.4")
        self.assertEqual(merged.cohort_counts.case_count, 40)
        # And the PS4 event from the cohort provider is still summed in.
        self.assertIn("PS4", [e.acmg_criterion for e in merged.events])

    def test_absent_fields_stay_none(self):
        # A provider that sets neither field leaves the merged bundle's fields None
        # (absence is recorded, never invented).
        class _Empty(EvidenceProvider):
            name = "empty"

            def fetch(self, case_or_variant):
                return EvidenceBundle(variant_key=VARIANT_KEY)

        resolver = EvidenceResolver({"empty": _Empty()})
        merged = resolver.resolve(VARIANT, variant_key=VARIANT_KEY)["bundle"]
        self.assertIsNone(merged.transcript)
        self.assertIsNone(merged.cohort_counts)


class _WiringApiBase(unittest.TestCase):
    def _client(self, providers) -> TestClient:
        settings = Settings(
            environment="development",
            legacy_default_roles=("viewer", "reviewer", "operator", "admin"),
        )
        app = create_app(
            settings=settings,
            store=InMemoryClinicalStore(),
            resolver=EvidenceResolver(providers),
        )
        return TestClient(app)

    def h(self):
        return {"X-Tenant-Id": str(uuid.uuid4())}


class TestEvidenceResolveSurface(_WiringApiBase):
    """``POST /evidence/resolve`` must expose transcript + cohort counts."""

    def test_resolve_surfaces_transcript_and_cohort_counts(self):
        client = self._client(
            {"tx": _TranscriptProvider(), "cohort": _CohortProvider()}
        )
        r = client.post(
            "/evidence/resolve",
            json={"variant": VARIANT, "providers": ["tx", "cohort"]},
            headers=self.h(),
        )
        self.assertEqual(r.status_code, 200, r.text)
        data = r.json()
        self.assertEqual(data["transcript"]["mane_select"], "NM_007294.4")
        self.assertEqual(data["cohort_counts"]["denominator"], 200)
        self.assertEqual(data["cohort_counts"]["case_count"], 40)
        # per-provider breakdown keeps each source's own view too.
        self.assertEqual(
            data["per_provider"]["tx"]["transcript"]["mane_select"], "NM_007294.4"
        )

    def test_resolve_transcript_absent_is_null_not_missing(self):
        client = self._client({"cohort": _CohortProvider()})
        r = client.post(
            "/evidence/resolve",
            json={"variant": VARIANT, "providers": ["cohort"]},
            headers=self.h(),
        )
        self.assertEqual(r.status_code, 200, r.text)
        data = r.json()
        self.assertIn("transcript", data)
        self.assertIsNone(data["transcript"])
        self.assertIsNotNone(data["cohort_counts"])


class TestClassifyCarriesEvidenceFields(_WiringApiBase):
    """The classify preview's ``evidence`` bundle must carry the same fields."""

    def test_classify_evidence_bundle_has_transcript_and_cohort(self):
        client = self._client(
            {"tx": _TranscriptProvider(), "cohort": _CohortProvider()}
        )
        body = {"variant": VARIANT, "evidence": {
            "resolve": {"variant": VARIANT, "providers": ["tx", "cohort"]}
        }}
        r = client.post("/classify", json=body, headers=self.h())
        self.assertEqual(r.status_code, 200, r.text)
        evidence = r.json()["evidence"]
        self.assertEqual(evidence["transcript"]["mane_select"], "NM_007294.4")
        self.assertEqual(evidence["cohort_counts"]["denominator"], 200)


class TestPersistedReceiptReports(_WiringApiBase):
    """A persisted receipt must carry the bundle through to reviewer + FHIR reports.

    This is the Job-1 -> Job-3 transport seam end to end: resolve through providers
    that attach transcript/cohort -> persist -> read the receipt's reports and see
    those fields surfaced (previously the receipt dropped the bundle, so reports
    could never show them in production).
    """

    def _persist(self, client, headers):
        body = {"variant": VARIANT, "evidence": {
            "resolve": {"variant": VARIANT, "providers": ["tx", "cohort"]}
        }}
        r = client.post("/classifications", json=body, headers=headers)
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()["receipt"]["classification_id"]

    def test_receipt_persists_evidence_bundle(self):
        client = self._client({"tx": _TranscriptProvider(), "cohort": _CohortProvider()})
        headers = self.h()
        cid = self._persist(client, headers)
        receipt = client.get(f"/classifications/{cid}", headers=headers).json()
        self.assertEqual(receipt["evidence"]["transcript"]["mane_select"], "NM_007294.4")
        self.assertEqual(receipt["evidence"]["cohort_counts"]["denominator"], 200)

    def test_reviewer_report_surfaces_transcript_and_cohort(self):
        client = self._client({"tx": _TranscriptProvider(), "cohort": _CohortProvider()})
        headers = self.h()
        cid = self._persist(client, headers)
        report = client.get(
            f"/classifications/{cid}/report/reviewer", headers=headers
        ).json()
        ext = report["evidence_extensions"]
        self.assertEqual(ext["transcript"]["mane_select"], "NM_007294.4")
        self.assertEqual(ext["cohort_counts"]["denominator"], 200)
        # provider provenance is now populated too (was empty before persistence wiring)
        self.assertIn("cohort", report["evidence_provenance"]["provider_versions"])

    def test_reviewer_markdown_renders_transcript_section(self):
        client = self._client({"tx": _TranscriptProvider(), "cohort": _CohortProvider()})
        headers = self.h()
        cid = self._persist(client, headers)
        md = client.get(
            f"/classifications/{cid}/report/reviewer?format=markdown", headers=headers
        ).text
        self.assertIn("Transcript & cohort context", md)
        self.assertIn("NM_007294.4", md)
        self.assertIn("PS4 cohort counts", md)

    def test_fhir_report_carries_mane_transcript(self):
        client = self._client({"tx": _TranscriptProvider(), "cohort": _CohortProvider()})
        headers = self.h()
        cid = self._persist(client, headers)
        bundle = client.get(
            f"/classifications/{cid}/report/fhir", headers=headers
        ).json()
        # find the MANE transcript component anywhere in the variant observation
        values = []
        for entry in bundle["entry"]:
            for comp in entry["resource"].get("component", []):
                if comp.get("code", {}).get("coding", [{}])[0].get("code") == "mane-transcript":
                    values.append(comp.get("valueString"))
        self.assertEqual(values, ["NM_007294.4"])

    def test_direct_events_receipt_has_no_evidence_bundle(self):
        # A receipt scored from direct events (no resolver) carries no bundle, so the
        # reports degrade cleanly to no transcript/cohort context.
        client = self._client({"tx": _TranscriptProvider()})
        headers = self.h()
        body = {"variant": VARIANT, "evidence": {"events": [
            {"source": "curated", "acmg_criterion": "PVS1",
             "evidence_direction": "pathogenic", "applied_strength": "very_strong"},
        ]}}
        r = client.post("/classifications", json=body, headers=headers)
        self.assertEqual(r.status_code, 201, r.text)
        cid = r.json()["receipt"]["classification_id"]
        receipt = client.get(f"/classifications/{cid}", headers=headers).json()
        self.assertIsNone(receipt["evidence"])
        report = client.get(
            f"/classifications/{cid}/report/reviewer", headers=headers
        ).json()
        self.assertIsNone(report["evidence_extensions"]["transcript"])
        self.assertIsNone(report["evidence_extensions"]["cohort_counts"])


if __name__ == "__main__":
    unittest.main()
