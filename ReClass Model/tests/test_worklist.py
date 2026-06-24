"""Variant case worklist: model, store, and API tests.

Runs fully in-memory (no PostgreSQL): the API is built with an injected
``InMemoryClinicalStore`` and the worklist store falls back to its in-memory
double, exactly like ``tests/test_workbench``. Covers the case state machine, the
SLA/turnaround clock, the PHI boundary (de-identified queue vs. ``case:read_phi``
detail), tenant isolation, and the full router surface + RBAC.
"""

from __future__ import annotations

import os
import sys
import unittest
import uuid
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402

from api.app import create_app  # noqa: E402
from api.auth import issue_jwt  # noqa: E402
from api.settings import Settings  # noqa: E402
from api.store import InMemoryClinicalStore  # noqa: E402
from worklist.case import (  # noqa: E402
    Case,
    CaseError,
    InMemoryWorklistStore,
    case_view,
    redact_phi,
    sla_view,
)

T0 = datetime(2026, 6, 17, 0, 0, tzinfo=timezone.utc)


def _case(**over):
    kw = dict(accession="ACC-1", tenant_id="t-a", priority="routine")
    kw.update(over)
    return Case(**kw)


# --------------------------------------------------------------------------- #
# Model                                                                       #
# --------------------------------------------------------------------------- #
class TestCaseModel(unittest.TestCase):
    def test_requires_accession(self) -> None:
        with self.assertRaises(CaseError):
            Case(accession="", tenant_id="t-a")

    def test_rejects_bad_priority_and_status(self) -> None:
        with self.assertRaises(CaseError):
            _case(priority="whenever")
        with self.assertRaises(CaseError):
            _case(status="archived")

    def test_due_at_derived_from_received_and_priority(self) -> None:
        c = _case(priority="stat", received_at=T0.isoformat())
        # stat target is 24h
        self.assertEqual(c.due_at, (T0 + timedelta(hours=24)).isoformat())

    def test_explicit_due_at_is_kept(self) -> None:
        explicit = (T0 + timedelta(hours=5)).isoformat()
        c = _case(priority="routine", received_at=T0.isoformat(), due_at=explicit)
        self.assertEqual(c.due_at, explicit)

    def test_rejects_non_iso_timestamp(self) -> None:
        with self.assertRaises(CaseError):
            _case(received_at="last tuesday")

    def test_classification_ids_deduped_in_order(self) -> None:
        c = _case(classification_ids=["b", "a", "b", "a"])
        self.assertEqual(c.classification_ids, ["b", "a"])

    def test_to_from_dict_roundtrip(self) -> None:
        c = _case(received_at=T0.isoformat(), patient_mrn="MRN-9")
        again = Case.from_dict(c.to_dict())
        self.assertEqual(again.to_dict(), c.to_dict())


class TestSlaAndRedaction(unittest.TestCase):
    def test_sla_overdue_due_soon_on_track(self) -> None:
        row = _case(received_at=T0.isoformat(), priority="routine").to_dict()
        due = datetime.fromisoformat(row["due_at"])
        self.assertEqual(sla_view(row, as_of=due + timedelta(hours=1))["sla_status"], "overdue")
        self.assertEqual(sla_view(row, as_of=due - timedelta(hours=2))["sla_status"], "due_soon")
        self.assertEqual(sla_view(row, as_of=due - timedelta(days=5))["sla_status"], "on_track")

    def test_sla_none_when_no_due(self) -> None:
        row = _case().to_dict()  # no received_at -> no due_at
        self.assertEqual(sla_view(row, as_of=T0)["sla_status"], "none")

    def test_turnaround_hours_uses_release_time(self) -> None:
        row = _case(received_at=T0.isoformat(), status="released").to_dict()
        row["released_at"] = (T0 + timedelta(hours=10)).isoformat()
        self.assertEqual(sla_view(row, as_of=T0 + timedelta(hours=99))["turnaround_hours"], 10.0)
        self.assertEqual(sla_view(row, as_of=T0)["sla_status"], "released")

    def test_redact_phi_nulls_phi_and_flags(self) -> None:
        row = _case(patient_mrn="MRN-9", patient_name="Jane", indication="seizures").to_dict()
        red = redact_phi(row)
        self.assertIsNone(red["patient_mrn"])
        self.assertIsNone(red["patient_name"])
        self.assertIsNone(red["indication"])
        self.assertTrue(red["phi_redacted"])

    def test_case_view_includes_phi_when_requested(self) -> None:
        row = _case(patient_mrn="MRN-9").to_dict()
        self.assertEqual(case_view(row, include_phi=True)["patient_mrn"], "MRN-9")
        self.assertIsNone(case_view(row, include_phi=False)["patient_mrn"])


# --------------------------------------------------------------------------- #
# In-memory store                                                             #
# --------------------------------------------------------------------------- #
class TestInMemoryWorklistStore(unittest.TestCase):
    def setUp(self) -> None:
        self.store = InMemoryWorklistStore()

    def test_create_assigns_id_and_history(self) -> None:
        row = self.store.create_case(_case())
        self.assertTrue(row["case_id"])
        self.assertEqual(row["status"], "draft")
        self.assertEqual(row["history"][0]["to"], "draft")

    def test_get_redacts_phi_by_default(self) -> None:
        cid = self.store.create_case(_case(patient_mrn="MRN-9"))["case_id"]
        self.assertIsNone(self.store.get_case(tenant_id="t-a", case_id=cid)["patient_mrn"])
        self.assertEqual(
            self.store.get_case(tenant_id="t-a", case_id=cid, include_phi=True)["patient_mrn"],
            "MRN-9",
        )

    def test_list_never_includes_phi(self) -> None:
        self.store.create_case(_case(patient_mrn="MRN-9"))
        rows = self.store.list_cases(tenant_id="t-a")
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0]["patient_mrn"])
        self.assertTrue(rows[0]["phi_redacted"])

    def test_filters_status_priority_unassigned_and_search(self) -> None:
        self.store.create_case(_case(accession="ACC-1", priority="stat", assigned_to="sci-7"))
        self.store.create_case(_case(accession="ACC-2", priority="routine", ordering_provider="Dr. Heart"))
        self.assertEqual(len(self.store.list_cases(tenant_id="t-a", priority="stat")), 1)
        self.assertEqual(len(self.store.list_cases(tenant_id="t-a", unassigned=True)), 1)
        self.assertEqual(len(self.store.list_cases(tenant_id="t-a", query="heart")), 1)
        self.assertEqual(len(self.store.list_cases(tenant_id="t-a", query="ACC")), 2)

    def test_transition_pipeline_and_timestamps(self) -> None:
        cid = self.store.create_case(_case())["case_id"]
        self.store.transition_case(tenant_id="t-a", case_id=cid, to_status="in_review", actor="r1")
        signed = self.store.transition_case(tenant_id="t-a", case_id=cid, to_status="signed", actor="r1")
        self.assertEqual(signed["status"], "signed")
        self.assertIsNotNone(signed["signed_at"])
        released = self.store.transition_case(tenant_id="t-a", case_id=cid, to_status="released", actor="r1")
        self.assertIsNotNone(released["released_at"])
        self.assertEqual(released["history"][-1]["from"], "signed")

    def test_illegal_transition_rejected(self) -> None:
        cid = self.store.create_case(_case())["case_id"]
        with self.assertRaises(CaseError):
            self.store.transition_case(tenant_id="t-a", case_id=cid, to_status="released")

    def test_transition_same_state_rejected(self) -> None:
        cid = self.store.create_case(_case())["case_id"]
        with self.assertRaises(CaseError):
            self.store.transition_case(tenant_id="t-a", case_id=cid, to_status="draft")

    def test_update_assign_and_priority(self) -> None:
        cid = self.store.create_case(_case())["case_id"]
        row = self.store.update_case(tenant_id="t-a", case_id=cid, assigned_to="sci-7", priority="urgent")
        self.assertEqual(row["assigned_to"], "sci-7")
        self.assertEqual(row["priority"], "urgent")
        # unassign with explicit None
        row = self.store.update_case(tenant_id="t-a", case_id=cid, assigned_to=None)
        self.assertIsNone(row["assigned_to"])

    def test_update_bad_priority_rejected(self) -> None:
        cid = self.store.create_case(_case())["case_id"]
        with self.assertRaises(CaseError):
            self.store.update_case(tenant_id="t-a", case_id=cid, priority="someday")

    def test_attach_classification_dedupes(self) -> None:
        cid = self.store.create_case(_case())["case_id"]
        self.store.attach_classification(tenant_id="t-a", case_id=cid, classification_id="x")
        row = self.store.attach_classification(tenant_id="t-a", case_id=cid, classification_id="x")
        self.assertEqual(row["classification_ids"], ["x"])
        self.assertEqual(row["variant_count"], 1)

    def test_tenant_isolation(self) -> None:
        self.store.create_case(_case(tenant_id="t-a"))
        self.assertEqual(len(self.store.list_cases(tenant_id="t-b")), 0)
        cid = self.store.create_case(_case(tenant_id="t-a"))["case_id"]
        self.assertIsNone(self.store.get_case(tenant_id="t-b", case_id=cid))
        with self.assertRaises(LookupError):
            self.store.transition_case(tenant_id="t-b", case_id=cid, to_status="in_review")

    def test_metrics_summarize_queue(self) -> None:
        self.store.create_case(_case(accession="A1", priority="stat"))
        c2 = self.store.create_case(_case(accession="A2", assigned_to="sci-7"))["case_id"]
        self.store.transition_case(tenant_id="t-a", case_id=c2, to_status="in_review")
        m = self.store.metrics(tenant_id="t-a")
        self.assertEqual(m["total"], 2)
        self.assertEqual(m["by_status"]["draft"], 1)
        self.assertEqual(m["by_status"]["in_review"], 1)
        self.assertEqual(m["unassigned"], 1)

    # ----- bulk operations ------------------------------------------------- #
    def test_bulk_assign_partial_success(self) -> None:
        a = self.store.create_case(_case(accession="A1"))["case_id"]
        b = self.store.create_case(_case(accession="A2"))["case_id"]
        result = self.store.bulk_assign(
            tenant_id="t-a", case_ids=[a, b, "missing"], assigned_to="sci-7", actor="r1"
        )
        self.assertEqual(result["summary"], {"requested": 3, "succeeded": 2, "failed": 1})
        self.assertEqual([r["ok"] for r in result["results"]], [True, True, False])
        self.assertEqual(result["results"][2]["error_code"], "not_found")
        # the two real cases were actually assigned
        self.assertEqual(self.store.get_case(tenant_id="t-a", case_id=a)["assigned_to"], "sci-7")

    def test_bulk_assign_can_unassign(self) -> None:
        a = self.store.create_case(_case(assigned_to="sci-7"))["case_id"]
        result = self.store.bulk_assign(tenant_id="t-a", case_ids=[a], assigned_to=None)
        self.assertIsNone(result["results"][0]["assigned_to"])
        self.assertEqual(result["assigned_to"], None)

    def test_bulk_transition_independent_validation(self) -> None:
        draft = self.store.create_case(_case(accession="A1"))["case_id"]
        ready = self.store.create_case(_case(accession="A2"))["case_id"]
        self.store.transition_case(tenant_id="t-a", case_id=ready, to_status="in_review")
        # draft -> in_review is legal; in_review -> in_review is "already" -> rejected
        result = self.store.bulk_transition(
            tenant_id="t-a", case_ids=[draft, ready], to_status="in_review", actor="r1"
        )
        self.assertEqual(result["summary"], {"requested": 2, "succeeded": 1, "failed": 1})
        ok = [r for r in result["results"] if r["ok"]][0]
        self.assertEqual(ok["case_id"], draft)
        self.assertEqual(ok["status"], "in_review")
        bad = [r for r in result["results"] if not r["ok"]][0]
        self.assertEqual(bad["error_code"], "rejected")

    def test_bulk_transition_illegal_target_rejected_per_case(self) -> None:
        a = self.store.create_case(_case())["case_id"]  # draft
        # draft -> released is not an allowed transition
        result = self.store.bulk_transition(tenant_id="t-a", case_ids=[a], to_status="released")
        self.assertEqual(result["summary"]["succeeded"], 0)
        self.assertEqual(result["results"][0]["error_code"], "rejected")

    def test_bulk_respects_tenant_isolation(self) -> None:
        a = self.store.create_case(_case(tenant_id="t-a"))["case_id"]
        result = self.store.bulk_assign(tenant_id="t-b", case_ids=[a], assigned_to="x")
        self.assertEqual(result["summary"]["failed"], 1)
        self.assertEqual(result["results"][0]["error_code"], "not_found")


# --------------------------------------------------------------------------- #
# API                                                                         #
# --------------------------------------------------------------------------- #
class TestWorklistApi(unittest.TestCase):
    def setUp(self) -> None:
        self.secret = "worklist-secret"
        self.tenant_a = str(uuid.uuid4())
        self.tenant_b = str(uuid.uuid4())
        settings = Settings(environment="production", jwt_secret=self.secret)
        self.client = TestClient(create_app(settings=settings, store=InMemoryClinicalStore()))

    def auth(self, tenant_id, roles, user_id="wl-user"):
        token = issue_jwt(user_id=user_id, tenant_id=tenant_id, roles=roles,
                          secret=self.secret, display_name=user_id)
        return {"Authorization": f"Bearer {token}"}

    def _create(self, tenant=None, roles=("reviewer",), **body):
        payload = {"accession": "ACC-1", "priority": "stat", "patient_mrn": "MRN-9"}
        payload.update(body)
        return self.client.post(
            "/worklist/cases", json=payload,
            headers=self.auth(tenant or self.tenant_a, list(roles)),
        )

    def test_create_and_list_de_identified(self) -> None:
        resp = self._create()
        self.assertEqual(resp.status_code, 201, resp.text)
        self.assertTrue(resp.json()["case_id"])

        listed = self.client.get("/worklist/cases", headers=self.auth(self.tenant_a, ["viewer"]))
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(len(listed.json()), 1)
        # The de-identified queue never carries PHI.
        self.assertIsNone(listed.json()[0]["patient_mrn"])
        self.assertTrue(listed.json()[0]["phi_redacted"])

    def test_viewer_cannot_create(self) -> None:
        self.assertEqual(self._create(roles=("viewer",)).status_code, 403)

    def test_phi_boundary_on_detail(self) -> None:
        cid = self._create().json()["case_id"]
        # default detail is redacted even for a reviewer
        plain = self.client.get(f"/worklist/cases/{cid}", headers=self.auth(self.tenant_a, ["reviewer"]))
        self.assertIsNone(plain.json()["patient_mrn"])
        # viewer cannot request PHI
        denied = self.client.get(f"/worklist/cases/{cid}?include_phi=true",
                                 headers=self.auth(self.tenant_a, ["viewer"]))
        self.assertEqual(denied.status_code, 403)
        # reviewer with case:read_phi can
        allowed = self.client.get(f"/worklist/cases/{cid}?include_phi=true",
                                  headers=self.auth(self.tenant_a, ["reviewer"]))
        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(allowed.json()["patient_mrn"], "MRN-9")

    def test_update_requires_a_field(self) -> None:
        cid = self._create().json()["case_id"]
        empty = self.client.patch(f"/worklist/cases/{cid}", json={},
                                  headers=self.auth(self.tenant_a, ["reviewer"]))
        self.assertEqual(empty.status_code, 422)
        ok = self.client.patch(f"/worklist/cases/{cid}", json={"assigned_to": "sci-7"},
                               headers=self.auth(self.tenant_a, ["reviewer"]))
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(ok.json()["assigned_to"], "sci-7")

    def test_transition_legal_illegal_and_missing(self) -> None:
        cid = self._create().json()["case_id"]
        ok = self.client.post(f"/worklist/cases/{cid}/transition", json={"to_status": "in_review"},
                              headers=self.auth(self.tenant_a, ["reviewer"]))
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(ok.json()["status"], "in_review")
        bad = self.client.post(f"/worklist/cases/{cid}/transition", json={"to_status": "released"},
                               headers=self.auth(self.tenant_a, ["reviewer"]))
        self.assertEqual(bad.status_code, 422)
        missing = self.client.post("/worklist/cases/does-not-exist/transition",
                                   json={"to_status": "in_review"},
                                   headers=self.auth(self.tenant_a, ["reviewer"]))
        self.assertEqual(missing.status_code, 404)

    def test_viewer_cannot_transition(self) -> None:
        cid = self._create().json()["case_id"]
        resp = self.client.post(f"/worklist/cases/{cid}/transition", json={"to_status": "in_review"},
                                headers=self.auth(self.tenant_a, ["viewer"]))
        self.assertEqual(resp.status_code, 403)

    def test_attach_classification_validates_existence(self) -> None:
        cid = self._create().json()["case_id"]
        # unknown classification -> 404
        missing = self.client.post(
            f"/worklist/cases/{cid}/classifications",
            json={"classification_id": str(uuid.uuid4())},
            headers=self.auth(self.tenant_a, ["reviewer"]),
        )
        self.assertEqual(missing.status_code, 404)
        # persist a real classification, then attach it
        persisted = self.client.post(
            "/classifications",
            json={
                "variant": {"chrom": "1", "pos": 100, "ref": "A", "alt": "G"},
                "evidence": {"events": [{"source": "revel", "acmg_criterion": "PP3",
                                         "evidence_direction": "pathogenic",
                                         "applied_strength": "supporting"}]},
            },
            headers=self.auth(self.tenant_a, ["reviewer"]),
        )
        self.assertEqual(persisted.status_code, 201, persisted.text)
        classification_id = persisted.json()["receipt"]["classification_id"]
        attached = self.client.post(
            f"/worklist/cases/{cid}/classifications",
            json={"classification_id": classification_id},
            headers=self.auth(self.tenant_a, ["reviewer"]),
        )
        self.assertEqual(attached.status_code, 200, attached.text)
        self.assertIn(classification_id, attached.json()["classification_ids"])
        self.assertEqual(attached.json()["variant_count"], 1)

    def test_metrics_endpoint(self) -> None:
        self._create()
        m = self.client.get("/worklist/metrics", headers=self.auth(self.tenant_a, ["operator"]))
        self.assertEqual(m.status_code, 200)
        self.assertEqual(m.json()["total"], 1)
        self.assertIn("by_status", m.json())

    def test_tenant_isolation(self) -> None:
        self._create(tenant=self.tenant_a)
        other = self.client.get("/worklist/cases", headers=self.auth(self.tenant_b, ["reviewer"]))
        self.assertEqual(other.json(), [])

    def test_invalid_priority_filter_rejected(self) -> None:
        resp = self.client.get("/worklist/cases?priority=someday",
                               headers=self.auth(self.tenant_a, ["viewer"]))
        self.assertEqual(resp.status_code, 422)

    # ----- bulk actions ---------------------------------------------------- #
    def _make(self, accession):
        return self._create(accession=accession).json()["case_id"]

    def test_bulk_assign_endpoint(self) -> None:
        a, b = self._make("A1"), self._make("A2")
        resp = self.client.post(
            "/worklist/cases/bulk/assign",
            json={"case_ids": [a, b, "missing"], "assigned_to": "sci-7"},
            headers=self.auth(self.tenant_a, ["reviewer"]),
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["summary"], {"requested": 3, "succeeded": 2, "failed": 1})

    def test_bulk_assign_deduplicates_ids(self) -> None:
        a = self._make("A1")
        resp = self.client.post(
            "/worklist/cases/bulk/assign",
            json={"case_ids": [a, a, a], "assigned_to": "sci-7"},
            headers=self.auth(self.tenant_a, ["reviewer"]),
        )
        # the same case selected three times is applied (and reported) once
        self.assertEqual(resp.json()["summary"], {"requested": 1, "succeeded": 1, "failed": 0})

    def test_bulk_assign_requires_assigned_to_field(self) -> None:
        a = self._make("A1")
        resp = self.client.post(
            "/worklist/cases/bulk/assign", json={"case_ids": [a]},
            headers=self.auth(self.tenant_a, ["reviewer"]),
        )
        self.assertEqual(resp.status_code, 422)

    def test_bulk_assign_empty_selection_rejected(self) -> None:
        resp = self.client.post(
            "/worklist/cases/bulk/assign", json={"case_ids": [], "assigned_to": "x"},
            headers=self.auth(self.tenant_a, ["reviewer"]),
        )
        self.assertEqual(resp.status_code, 422)

    def test_bulk_transition_partial_success(self) -> None:
        draft, ready = self._make("A1"), self._make("A2")
        self.client.post(f"/worklist/cases/{ready}/transition", json={"to_status": "in_review"},
                         headers=self.auth(self.tenant_a, ["reviewer"]))
        resp = self.client.post(
            "/worklist/cases/bulk/transition",
            json={"case_ids": [draft, ready], "to_status": "in_review"},
            headers=self.auth(self.tenant_a, ["reviewer"]),
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["summary"], {"requested": 2, "succeeded": 1, "failed": 1})

    def test_bulk_transition_bad_status_rejected(self) -> None:
        a = self._make("A1")
        resp = self.client.post(
            "/worklist/cases/bulk/transition",
            json={"case_ids": [a], "to_status": "archived"},
            headers=self.auth(self.tenant_a, ["reviewer"]),
        )
        self.assertEqual(resp.status_code, 422)

    def test_bulk_not_shadowed_by_case_id_route(self) -> None:
        # "bulk" must hit the bulk endpoint, not GET /worklist/cases/{case_id}.
        a = self._make("A1")
        resp = self.client.post(
            "/worklist/cases/bulk/transition",
            json={"case_ids": [a], "to_status": "in_review"},
            headers=self.auth(self.tenant_a, ["reviewer"]),
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("results", resp.json())

    def test_viewer_cannot_bulk_transition(self) -> None:
        a = self._make("A1")
        resp = self.client.post(
            "/worklist/cases/bulk/transition",
            json={"case_ids": [a], "to_status": "in_review"},
            headers=self.auth(self.tenant_a, ["viewer"]),
        )
        self.assertEqual(resp.status_code, 403)

    def test_bulk_actions_are_audited(self) -> None:
        a = self._make("A1")
        self.client.post(
            "/worklist/cases/bulk/assign", json={"case_ids": [a], "assigned_to": "sci-7"},
            headers=self.auth(self.tenant_a, ["reviewer"]),
        )
        audit = self.client.get("/audit?action=case.bulk_assign",
                                headers=self.auth(self.tenant_a, ["admin"]))
        self.assertEqual(audit.status_code, 200)
        self.assertEqual(len(audit.json()), 1)
        self.assertEqual(audit.json()[0]["detail"]["succeeded"], [a])

    def test_bulk_tenant_isolation(self) -> None:
        a = self._make("A1")  # belongs to tenant_a
        resp = self.client.post(
            "/worklist/cases/bulk/assign", json={"case_ids": [a], "assigned_to": "x"},
            headers=self.auth(self.tenant_b, ["reviewer"]),
        )
        # tenant B cannot see tenant A's case: it fails as not_found, not assigned
        self.assertEqual(resp.json()["summary"]["failed"], 1)
        self.assertEqual(resp.json()["results"][0]["error_code"], "not_found")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
