"""Evidence workbench: model, store, and API tests (job1 tasks 1-5).

Runs fully in-memory (no PostgreSQL): the API is built with an injected
``InMemoryClinicalStore`` and a deterministic resolver, exactly like
``tests/test_api_contract``. Covers reviewer-entered evidence persistence + provenance,
coverage breakdowns, curation enqueue/lifecycle, tenant isolation, and the import
surfaces.
"""

from __future__ import annotations

import os
import sys
import unittest
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402

from api.app import create_app  # noqa: E402
from api.auth import issue_jwt  # noqa: E402
from api.evidence_resolver import EvidenceResolver  # noqa: E402
from api.settings import Settings  # noqa: E402
from api.store import InMemoryClinicalStore  # noqa: E402
from evidence.revel import RevelProvider  # noqa: E402
from evidence.workbench import (  # noqa: E402
    DbWorkbenchStore,
    InMemoryWorkbenchStore,
    ReviewerEvidence,
    WorkbenchError,
)
from evidence import coverage as coverage_mod  # noqa: E402
from evidence import curation as curation_mod  # noqa: E402


VARIANT = {"chrom": "1", "pos": 100, "ref": "A", "alt": "G"}


def _resolver() -> EvidenceResolver:
    resolver = EvidenceResolver()
    resolver.register("revel", RevelProvider.from_scores({"1-100-A-G": 0.95}))
    return resolver


# --------------------------------------------------------------------------- #
# Model + store (no API)                                                       #
# --------------------------------------------------------------------------- #
class TestReviewerEvidenceModel(unittest.TestCase):
    def test_requires_strength_or_points(self) -> None:
        with self.assertRaises(WorkbenchError):
            ReviewerEvidence(
                variant_key="GRCh38-1-100-A-G", acmg_criterion="PS3",
                evidence_direction="pathogenic", reviewer="r",
            )

    def test_checksum_is_deterministic(self) -> None:
        kw = dict(variant_key="GRCh38-1-100-A-G", acmg_criterion="ps3",
                  evidence_direction="pathogenic", applied_strength="strong",
                  reviewer="Dr. R", record={"assay": "MAVE", "oddspath": 25.0})
        a = ReviewerEvidence(**kw)
        b = ReviewerEvidence(**kw)
        self.assertEqual(a.checksum, b.checksum)
        self.assertEqual(a.acmg_criterion, "PS3")  # upper-cased

    def test_to_event_carries_provenance_outside_hash(self) -> None:
        ev = ReviewerEvidence(
            variant_key="GRCh38-1-100-A-G", acmg_criterion="PS3",
            evidence_direction="pathogenic", applied_strength="strong",
            reviewer="Dr. R", source_version="lab-v1", access_date="2026-06-17",
        )
        event = ev.to_event()
        self.assertEqual(event.acmg_criterion, "PS3")
        self.assertEqual(event.applied_strength, "strong")
        self.assertIsNone(event.points)  # strength-derived -> points stay None
        self.assertEqual(event.raw["provenance"]["reviewer"], "Dr. R")
        self.assertEqual(event.raw["provenance"]["checksum"], ev.checksum)


class TestInMemoryWorkbenchStore(unittest.TestCase):
    def setUp(self) -> None:
        self.store = InMemoryWorkbenchStore()

    def _evidence(self, **over):
        kw = dict(variant_key="GRCh38-1-100-A-G", acmg_criterion="PS3",
                  evidence_direction="pathogenic", applied_strength="strong",
                  reviewer="Dr. R")
        kw.update(over)
        return ReviewerEvidence(**kw)

    def test_add_and_list_evidence(self) -> None:
        row = self.store.add_evidence(self._evidence())
        self.assertIsNotNone(row["reviewer_evidence_id"])
        self.assertIsNotNone(row["entered_at"])
        listed = self.store.list_evidence(variant_key="GRCh38-1-100-A-G")
        self.assertEqual(len(listed), 1)
        self.assertEqual(self.store.list_evidence(status="withdrawn"), [])

    def test_set_status_and_expire_due(self) -> None:
        row = self.store.add_evidence(
            self._evidence(expires_at="2020-01-01T00:00:00+00:00")
        )
        rid = row["reviewer_evidence_id"]
        flipped = self.store.expire_due(as_of="2026-06-19T00:00:00+00:00")
        self.assertEqual(flipped, [rid])
        self.assertEqual(self.store.get_evidence(rid)["status"], "expired")
        updated = self.store.set_status(rid, "withdrawn")
        self.assertEqual(updated["status"], "withdrawn")

    def test_coverage_upsert_and_summary(self) -> None:
        t = "tenant-a"
        rec = coverage_mod.compute_coverage(
            "GRCh38-1-1-A-G", ["PVS1"], variant_class="lof", gene="BRCA1")
        self.store.upsert_coverage(tenant_id=t, record=rec)
        # Re-upsert same variant -> still one row (stable coverage_id).
        first_id = self.store.list_coverage(tenant_id=t)[0]["coverage_id"]
        self.store.upsert_coverage(tenant_id=t, record=rec)
        rows = self.store.list_coverage(tenant_id=t)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["coverage_id"], first_id)
        summary = self.store.coverage_summary(tenant_id=t)
        self.assertEqual(summary["total"], 1)
        self.assertEqual(summary["blocked"], 1)

    def test_coverage_tenant_isolation(self) -> None:
        rec = coverage_mod.compute_coverage("GRCh38-1-1-A-G", [], variant_class="lof")
        self.store.upsert_coverage(tenant_id="a", record=rec)
        self.assertEqual(self.store.list_coverage(tenant_id="b"), [])

    def test_curation_enqueue_dedupes_open(self) -> None:
        item = curation_mod.CurationItem(kind="missing_transcript",
                                         variant_key="GRCh38-1-1-A-G")
        first = self.store.enqueue_curation(tenant_id="a", item=item)
        dup = self.store.enqueue_curation(tenant_id="a", item=item)
        self.assertIsNotNone(first)
        self.assertIsNone(dup)  # second open (variant, kind) is a no-op
        self.assertEqual(len(self.store.list_curation(tenant_id="a")), 1)

    def test_curation_state_transition_stamps_resolved(self) -> None:
        item = curation_mod.CurationItem(kind="missing_transcript",
                                         variant_key="GRCh38-1-1-A-G")
        row = self.store.enqueue_curation(tenant_id="a", item=item)
        updated = self.store.set_curation_state(
            tenant_id="a", curation_id=row["curation_id"], state="resolved")
        self.assertEqual(updated["state"], "resolved")
        self.assertIsNotNone(updated["resolved_at"])


class TestWorkbenchAppWiring(unittest.TestCase):
    def test_db_backend_installs_db_workbench_store(self) -> None:
        app = create_app(
            settings=Settings(
                environment="development",
                audit_backend="db",
                db_name="not_connected_during_factory",
            ),
            store=InMemoryClinicalStore(),
            resolver=EvidenceResolver(),
            audit_log=object(),
            admin_store=object(),
            webhook_store=object(),
        )
        self.assertIsInstance(app.state.workbench_store, DbWorkbenchStore)

    def test_injected_workbench_store_is_preserved(self) -> None:
        workbench_store = InMemoryWorkbenchStore()
        app = create_app(
            settings=Settings(environment="development"),
            store=InMemoryClinicalStore(),
            resolver=EvidenceResolver(),
            workbench_store=workbench_store,
        )
        self.assertIs(app.state.workbench_store, workbench_store)


# --------------------------------------------------------------------------- #
# API                                                                          #
# --------------------------------------------------------------------------- #
class TestWorkbenchApi(unittest.TestCase):
    def setUp(self) -> None:
        self.secret = "workbench-secret"
        self.tenant_a = str(uuid.uuid4())
        self.tenant_b = str(uuid.uuid4())
        settings = Settings(environment="production", jwt_secret=self.secret)
        app = create_app(settings=settings, store=InMemoryClinicalStore(),
                         resolver=_resolver())
        self.client = TestClient(app)

    def auth(self, tenant_id, roles, user_id="wb-user"):
        token = issue_jwt(user_id=user_id, tenant_id=tenant_id, roles=roles,
                          secret=self.secret, display_name=user_id)
        return {"Authorization": f"Bearer {token}"}

    # -- reviewer evidence -------------------------------------------------- #
    def _submit_evidence(self, tenant=None, roles=("reviewer",)):
        return self.client.post(
            "/evidence/workbench/evidence",
            json={
                "variant": VARIANT, "acmg_criterion": "PS3",
                "evidence_direction": "pathogenic", "applied_strength": "strong",
                "source_version": "lab-v1", "access_date": "2026-06-17",
                "reviewer": "Dr. Reviewer, PhD", "record": {"assay": "MAVE"},
            },
            headers=self.auth(tenant or self.tenant_a, list(roles)),
        )

    def test_submit_and_list_reviewer_evidence(self) -> None:
        resp = self._submit_evidence()
        self.assertEqual(resp.status_code, 201, resp.text)
        row = resp.json()
        self.assertEqual(row["acmg_criterion"], "PS3")
        self.assertTrue(row["checksum"])
        self.assertEqual(row["reviewer"], "Dr. Reviewer, PhD")

        listed = self.client.get(
            "/evidence/workbench/evidence",
            params={"variant_key": "GRCh38-1-100-A-G"},
            headers=self.auth(self.tenant_a, ["viewer"]),
        )
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(len(listed.json()), 1)

    def test_viewer_cannot_submit_evidence(self) -> None:
        resp = self._submit_evidence(roles=("viewer",))
        self.assertEqual(resp.status_code, 403)

    def test_evidence_status_update_and_404(self) -> None:
        rid = self._submit_evidence().json()["reviewer_evidence_id"]
        ok = self.client.post(
            f"/evidence/workbench/evidence/{rid}/status",
            json={"status": "withdrawn"},
            headers=self.auth(self.tenant_a, ["reviewer"]),
        )
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(ok.json()["status"], "withdrawn")
        missing = self.client.post(
            "/evidence/workbench/evidence/does-not-exist/status",
            json={"status": "withdrawn"},
            headers=self.auth(self.tenant_a, ["reviewer"]),
        )
        self.assertEqual(missing.status_code, 404)

    def test_evidence_requires_locus(self) -> None:
        resp = self.client.post(
            "/evidence/workbench/evidence",
            json={"variant": {"variation_id": "12345"}, "acmg_criterion": "PS3",
                  "evidence_direction": "pathogenic", "applied_strength": "strong"},
            headers=self.auth(self.tenant_a, ["reviewer"]),
        )
        self.assertEqual(resp.status_code, 422)

    # -- coverage ----------------------------------------------------------- #
    def test_coverage_record_and_summary(self) -> None:
        rec = self.client.post(
            "/evidence/coverage",
            json={"variant": VARIANT, "present_criteria": ["PVS1"],
                  "gene": "BRCA1", "variant_class": "lof", "provider": "clingen"},
            headers=self.auth(self.tenant_a, ["operator"]),
        )
        self.assertEqual(rec.status_code, 201, rec.text)
        self.assertTrue(rec.json()["blocked"])  # lof missing functional -> blocked

        summary = self.client.get(
            "/evidence/coverage", headers=self.auth(self.tenant_a, ["viewer"]))
        self.assertEqual(summary.status_code, 200)
        self.assertEqual(summary.json()["blocked"], 1)

        by_gene = self.client.get(
            "/evidence/coverage", params={"by": "gene"},
            headers=self.auth(self.tenant_a, ["viewer"]))
        self.assertEqual(by_gene.json()["buckets"]["BRCA1"]["blocked"], 1)

        bad = self.client.get(
            "/evidence/coverage", params={"by": "nope"},
            headers=self.auth(self.tenant_a, ["viewer"]))
        self.assertEqual(bad.status_code, 422)

    def test_coverage_tenant_isolation(self) -> None:
        self.client.post(
            "/evidence/coverage",
            json={"variant": VARIANT, "present_criteria": [], "variant_class": "lof"},
            headers=self.auth(self.tenant_a, ["operator"]))
        other = self.client.get(
            "/evidence/coverage", headers=self.auth(self.tenant_b, ["viewer"]))
        self.assertEqual(other.json()["total"], 0)

    # -- curation ----------------------------------------------------------- #
    def test_curation_scan_enqueue_list_and_state(self) -> None:
        scan = self.client.post(
            "/evidence/curation/scan",
            json={"variant": {"chrom": "9", "pos": 999, "ref": "A", "alt": "T"},
                  "providers": ["revel"], "enqueue": True},
            headers=self.auth(self.tenant_a, ["operator"]),
        )
        self.assertEqual(scan.status_code, 200, scan.text)
        kinds = [i["kind"] for i in scan.json()["items"]]
        self.assertIn("unmatched_identity", kinds)
        self.assertEqual(scan.json()["enqueued_count"], len(scan.json()["enqueued"]))
        self.assertGreaterEqual(scan.json()["enqueued_count"], 1)

        listed = self.client.get(
            "/evidence/curation", headers=self.auth(self.tenant_a, ["viewer"]))
        self.assertEqual(listed.status_code, 200)
        self.assertGreaterEqual(len(listed.json()), 1)
        curation_id = listed.json()[0]["curation_id"]

        moved = self.client.post(
            f"/evidence/curation/{curation_id}/state",
            json={"state": "dismissed"},
            headers=self.auth(self.tenant_a, ["operator"]))
        self.assertEqual(moved.status_code, 200)
        self.assertEqual(moved.json()["state"], "dismissed")

        bad = self.client.post(
            "/evidence/curation/scan",
            json={"variant": VARIANT, "providers": ["revel"]},
            headers=self.auth(self.tenant_a, ["viewer"]))
        self.assertEqual(bad.status_code, 403)

    def test_curation_tenant_isolation(self) -> None:
        self.client.post(
            "/evidence/curation/scan",
            json={"variant": {"chrom": "9", "pos": 999, "ref": "A", "alt": "T"},
                  "providers": ["revel"], "enqueue": True},
            headers=self.auth(self.tenant_a, ["operator"]))
        other = self.client.get(
            "/evidence/curation", headers=self.auth(self.tenant_b, ["viewer"]))
        self.assertEqual(other.json(), [])

    # -- import ------------------------------------------------------------- #
    def test_import_preview_vcf_dry_run(self) -> None:
        vcf = "1\t100\t.\tA\tG\n1\t100\t.\tA\tG\n2\t200\t.\tC\tT\n"
        resp = self.client.post(
            "/evidence/import/preview",
            json={"format": "vcf", "content": vcf, "resolve": True, "providers": ["revel"]},
            headers=self.auth(self.tenant_a, ["operator"]),
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        report = resp.json()
        self.assertTrue(report["dry_run"])
        self.assertEqual(report["totals"]["duplicate_rows"], 1)
        target = next(v for v in report["variants"] if v["key"] == "GRCh38-1-100-A-G")
        self.assertGreaterEqual(target["resolution"]["events"], 1)

    def test_import_batch_scrubs_phi(self) -> None:
        resp = self.client.post(
            "/evidence/import/batch",
            json={"source_kind": "functional", "access_date": "2026-06-17",
                  "records": [{"variant_key": "GRCh38-1-100-A-G", "mrn": "SECRET",
                               "result": "damaging", "oddspath": 25.0}]},
            headers=self.auth(self.tenant_a, ["operator"]),
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        report = resp.json()
        self.assertEqual(report["called"], 1)
        self.assertEqual(report["phi_fields_dropped"], 1)
        self.assertNotIn("SECRET", resp.text)

    def test_import_batch_unknown_kind_422(self) -> None:
        resp = self.client.post(
            "/evidence/import/batch",
            json={"source_kind": "nonsense", "records": []},
            headers=self.auth(self.tenant_a, ["operator"]))
        self.assertEqual(resp.status_code, 422)

    def test_workbench_criteria_endpoint(self) -> None:
        resp = self.client.get(
            "/evidence/workbench/criteria", headers=self.auth(self.tenant_a, ["viewer"]))
        self.assertEqual(resp.status_code, 200)
        self.assertIn("PVS1", resp.json())


if __name__ == "__main__":
    unittest.main()
