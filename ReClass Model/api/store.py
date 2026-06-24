"""Clinical persistence abstraction used by the API routers.

Every clinical read/write the API performs goes through a :class:`ClinicalStore`.
There are two implementations with identical semantics:

  * :class:`DbClinicalStore` — the real one. Each operation opens a
    ``storage.db.tenant_session`` (so PostgreSQL RLS scopes every query to the
    caller's tenant) and delegates to the existing ``storage.*`` repositories and
    ``monitoring.reanalysis``. It re-implements no clinical logic.

  * :class:`InMemoryClinicalStore` — a faithful, dependency-free double used by
    the test suite (and any environment without PostgreSQL). It reproduces the
    behaviours the acceptance criteria care about: tenant isolation, draft-vs-
    signed receipts, the reanalysis churn guard, crossing-only alerting, the
    same-tier audit trail, and the alert lifecycle.

Routers depend only on the abstract interface, so the same endpoints serve real
clinical traffic and run in CI without a database.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

from engine.scoring import EvidenceEvent, classify
from storage.alerts import ALERT_STATES, is_serious_crossing


# --------------------------------------------------------------------------- #
# Serialization helpers                                                       #
# --------------------------------------------------------------------------- #
def _jsonable(value: Any) -> Any:
    """Coerce DB/uuid/datetime values into JSON-friendly Python primitives."""
    if isinstance(value, (uuid.UUID,)):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


def serialize_receipt(row: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Normalize a classification row into the API's receipt shape.

    Adds ``is_draft`` (true until a credentialed sign-off lands) so no caller can
    mistake an unsigned receipt for a clinically released one.
    """
    if row is None:
        return None
    out = {k: _jsonable(v) for k, v in dict(row).items()}
    out["is_draft"] = out.get("signed_off_by") in (None, "")
    return out


# --------------------------------------------------------------------------- #
# Abstract interface                                                          #
# --------------------------------------------------------------------------- #
class ClinicalStore:
    """Interface the routers depend on. See module docstring for semantics."""

    def insert_classification(
        self, *, tenant_id: str, chrom: str, pos: int, ref: str, alt: str,
        build: str, classification, patient_mrn: Optional[str] = None,
        evidence: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        raise NotImplementedError

    def get_classification(self, *, tenant_id: str, classification_id: str) -> Optional[Dict[str, Any]]:
        raise NotImplementedError

    def list_classifications(
        self, *, tenant_id: str, variant_key: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        raise NotImplementedError

    def sign_off(
        self, *, tenant_id: str, classification_id: str, signed_off_by: str
    ) -> Dict[str, Any]:
        raise NotImplementedError

    def run_reanalysis(
        self, *, tenant_id: str, chrom: str, pos: int, ref: str, alt: str,
        build: str, new_events: List[EvidenceEvent], engine_version: str,
        trigger: str = "evidence", patient_mrn: Optional[str] = None,
    ) -> Dict[str, Any]:
        raise NotImplementedError

    def list_alerts(self, *, tenant_id: str, variant_key: Optional[str] = None) -> List[Dict[str, Any]]:
        raise NotImplementedError

    def get_alert(self, *, tenant_id: str, alert_id: str) -> Optional[Dict[str, Any]]:
        raise NotImplementedError

    def update_alert_state(self, *, tenant_id: str, alert_id: str, state: str) -> Dict[str, Any]:
        raise NotImplementedError

    def update_alert_triage(
        self,
        *,
        tenant_id: str,
        alert_id: str,
        triage: Dict[str, Any],
    ) -> Dict[str, Any]:
        raise NotImplementedError

    def list_reanalysis_events(
        self, *, tenant_id: str, variant_key: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        raise NotImplementedError

    def approve_release(
        self,
        *,
        tenant_id: str,
        classification_id: str,
        signoff_packet: Dict[str, Any],
    ) -> Dict[str, Any]:
        raise NotImplementedError

    def transition_release_state(
        self,
        *,
        tenant_id: str,
        classification_id: str,
        next_state: str,
        release_notes: Optional[str] = None,
    ) -> Dict[str, Any]:
        raise NotImplementedError

    def release_signoff_ledger(
        self, *, tenant_id: str, classification_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        raise NotImplementedError

    def list_reanalysis_queue(self, *, tenant_id: str) -> List[Dict[str, Any]]:
        raise NotImplementedError

    def list_reanalysis_runs(self, *, tenant_id: str) -> List[Dict[str, Any]]:
        raise NotImplementedError

    def get_reanalysis_policy(self, *, tenant_id: str) -> Dict[str, Any]:
        raise NotImplementedError

    def set_reanalysis_policy(self, *, tenant_id: str, policy: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError

    def record_amended_report(
        self,
        *,
        tenant_id: str,
        classification_id: str,
        lifecycle: Dict[str, Any],
    ) -> Dict[str, Any]:
        raise NotImplementedError


# --------------------------------------------------------------------------- #
# Real, DB-backed store                                                       #
# --------------------------------------------------------------------------- #
class DbClinicalStore(ClinicalStore):
    """RLS-enforced store: one ``tenant_session`` per operation.

    ``storage.classifications`` imports psycopg at module load, so it is imported
    lazily here — importing the API never requires the database driver.
    """

    def __init__(self, *, db_name: str = "reclass_dev", role: Optional[str] = None,
                 connect=None) -> None:
        self._db_name = db_name
        self._role = role
        self._connect = connect  # injectable for tests; defaults to storage.db.connect

    def _conn(self):
        if self._connect is not None:
            return self._connect()
        from storage.db import connect

        return connect(self._db_name)

    def _session(self, conn, tenant_id: str):
        from storage.db import tenant_session

        return tenant_session(conn, tenant_id, role=self._role)

    def insert_classification(self, *, tenant_id, chrom, pos, ref, alt, build,
                              classification, patient_mrn=None, evidence=None):
        from storage import classifications as cls

        with self._conn() as conn:
            with self._session(conn, tenant_id) as cur:
                variant_id = cls.upsert_variant(
                    cur, chrom=chrom, pos=pos, ref=ref, alt=alt, build=build
                )
                patient_id = None
                if patient_mrn:
                    patient_id = cls.insert_patient(cur, tenant_id=tenant_id, mrn=patient_mrn)
                cid = cls.insert_classification(
                    cur, tenant_id=tenant_id, variant_id=variant_id,
                    classification=classification, patient_id=patient_id,
                    evidence=evidence,
                )
                return serialize_receipt(cls.get_classification(cur, cid))

    def get_classification(self, *, tenant_id, classification_id):
        from storage import classifications as cls

        with self._conn() as conn:
            with self._session(conn, tenant_id) as cur:
                return serialize_receipt(cls.get_classification(cur, classification_id))

    def list_classifications(self, *, tenant_id, variant_key=None):
        from storage import classifications as cls

        with self._conn() as conn:
            with self._session(conn, tenant_id) as cur:
                variant_id = self._variant_id_for_key(cur, variant_key) if variant_key else None
                if variant_key and variant_id is None:
                    return []
                rows = cls.list_classifications(cur, variant_id=variant_id)
                return [serialize_receipt(r) for r in rows]

    def sign_off(self, *, tenant_id, classification_id, signed_off_by):
        from storage import classifications as cls

        with self._conn() as conn:
            with self._session(conn, tenant_id) as cur:
                existing = cls.get_classification(cur, classification_id)
                if existing is None:
                    raise LookupError(f"classification {classification_id} not visible")
                cls.sign_off(cur, classification_id, signed_off_by=signed_off_by)
                return serialize_receipt(cls.get_classification(cur, classification_id))

    def run_reanalysis(self, *, tenant_id, chrom, pos, ref, alt, build,
                       new_events, engine_version, trigger="evidence", patient_mrn=None):
        from dataclasses import asdict

        from storage import classifications as cls
        from monitoring.reanalysis import reanalyze

        with self._conn() as conn:
            with self._session(conn, tenant_id) as cur:
                variant_id = cls.upsert_variant(
                    cur, chrom=chrom, pos=pos, ref=ref, alt=alt, build=build
                )
                patient_id = None
                if patient_mrn:
                    patient_id = cls.insert_patient(cur, tenant_id=tenant_id, mrn=patient_mrn)
                result = reanalyze(
                    cur, tenant_id=tenant_id, variant_id=variant_id,
                    new_events=new_events, engine_version=engine_version,
                    trigger=trigger, patient_id=patient_id,
                )
                return _jsonable(asdict(result))

    def list_alerts(self, *, tenant_id, variant_key=None):
        from storage import alerts as al
        from storage import classifications as cls  # noqa: F401 (keeps import symmetry)

        with self._conn() as conn:
            with self._session(conn, tenant_id) as cur:
                variant_id = self._variant_id_for_key(cur, variant_key) if variant_key else None
                if variant_key and variant_id is None:
                    return []
                return [_jsonable(r) for r in al.list_alerts(cur, variant_id=variant_id)]

    def get_alert(self, *, tenant_id, alert_id):
        from storage import alerts as al

        with self._conn() as conn:
            with self._session(conn, tenant_id) as cur:
                row = al.get_alert(cur, alert_id)
                return _jsonable(row) if row is not None else None

    def update_alert_state(self, *, tenant_id, alert_id, state):
        from storage import alerts as al

        with self._conn() as conn:
            with self._session(conn, tenant_id) as cur:
                return _jsonable(al.update_alert_state(cur, alert_id, state=state))

    def update_alert_triage(self, *, tenant_id, alert_id, triage):
        from storage import alerts as al

        with self._conn() as conn:
            with self._session(conn, tenant_id) as cur:
                return _jsonable(al.update_alert_triage(
                    cur,
                    alert_id,
                    owner=triage.get("owner"),
                    sla_due_at=triage.get("sla_due_at"),
                    severity=triage.get("severity"),
                    resolution_rationale=triage.get("resolution_rationale"),
                    re_review_outcome=triage.get("re_review_outcome"),
                    notification_state=triage.get("notification_state"),
                ))

    def list_reanalysis_events(self, *, tenant_id, variant_key=None):
        from storage import alerts as al

        with self._conn() as conn:
            with self._session(conn, tenant_id) as cur:
                variant_id = self._variant_id_for_key(cur, variant_key) if variant_key else None
                if variant_key and variant_id is None:
                    return []
                return [_jsonable(r) for r in al.list_reanalysis_events(cur, variant_id=variant_id)]

    def approve_release(self, *, tenant_id, classification_id, signoff_packet):
        from storage import classifications as cls

        with self._conn() as conn:
            with self._session(conn, tenant_id) as cur:
                return serialize_receipt(cls.record_release_signoff(
                    cur, classification_id, signoff_packet=signoff_packet
                ))

    def transition_release_state(self, *, tenant_id, classification_id, next_state, release_notes=None):
        from storage import classifications as cls

        with self._conn() as conn:
            with self._session(conn, tenant_id) as cur:
                return serialize_receipt(cls.update_release_state(
                    cur, classification_id, next_state=next_state, release_notes=release_notes
                ))

    def release_signoff_ledger(self, *, tenant_id, classification_id=None):
        from storage import classifications as cls

        with self._conn() as conn:
            with self._session(conn, tenant_id) as cur:
                return [_jsonable(row) for row in cls.release_signoff_ledger(
                    cur, classification_id=classification_id
                )]

    def list_reanalysis_queue(self, *, tenant_id):
        from ops import queue as opsq

        with self._conn() as conn:
            with self._session(conn, tenant_id) as cur:
                return [_jsonable(row) for row in opsq.list_queue(cur)]

    def list_reanalysis_runs(self, *, tenant_id):
        from ops import run_report as opsr

        with self._conn() as conn:
            with self._session(conn, tenant_id) as cur:
                return [_jsonable(row) for row in opsr.list_runs(cur)]

    def get_reanalysis_policy(self, *, tenant_id):
        from ops import scheduler as opss

        with self._conn() as conn:
            with self._session(conn, tenant_id) as cur:
                return _jsonable(opss.get_tenant_policy(cur, tenant_id=tenant_id))

    def set_reanalysis_policy(self, *, tenant_id, policy):
        from ops import scheduler as opss

        with self._conn() as conn:
            with self._session(conn, tenant_id) as cur:
                return _jsonable(opss.upsert_tenant_policy(
                    cur,
                    tenant_id=tenant_id,
                    policy=opss.TenantReanalysisPolicy.from_dict(policy),
                ))

    def record_amended_report(self, *, tenant_id, classification_id, lifecycle):
        from psycopg.types.json import Jsonb

        with self._conn() as conn:
            with self._session(conn, tenant_id) as cur:
                report = lifecycle["report"]
                payload = lifecycle["payload"]
                cur.execute(
                    """
                    INSERT INTO clinical.amended_report (
                        tenant_id, classification_id, report_id, previous_report_id,
                        state, amendment_reason, payload_sha256, payload,
                        notification_state
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING *
                    """,
                    (
                        tenant_id,
                        classification_id,
                        report["report_id"],
                        report.get("previous_report_id"),
                        report["state"],
                        report.get("amendment_reason"),
                        report["payload_sha256"],
                        Jsonb(payload),
                        report.get("notification_state", "pending"),
                    ),
                )
                amended = cur.fetchone()
                notifications = []
                for notification in lifecycle.get("notifications", []):
                    cur.execute(
                        """
                        INSERT INTO clinical.clinician_notification (
                            tenant_id, amended_report_id, classification_id, recipient,
                            channel, notification_state, rationale
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                        RETURNING *
                        """,
                        (
                            tenant_id,
                            amended["amended_report_id"],
                            classification_id,
                            notification["recipient"],
                            notification.get("channel", "ehr"),
                            notification.get("notification_state", "pending"),
                            notification.get("rationale"),
                        ),
                    )
                    notifications.append(_jsonable(cur.fetchone()))
                # webhook-seam: emit report.amended and clinician_notification.pending.
                return {
                    "report": _jsonable(amended),
                    "notifications": notifications,
                    "payload": payload,
                }

    @staticmethod
    def _variant_id_for_key(cur, variant_key: str) -> Optional[str]:
        """Look up a clinical.variant id from a canonical variant_key string."""
        try:
            build, chrom, pos, ref, alt = variant_key.split("-", 4)
        except ValueError:
            return None
        cur.execute(
            "SELECT variant_id FROM clinical.variant "
            "WHERE build = %s AND chrom = %s AND pos = %s AND ref = %s AND alt = %s",
            (build, chrom, int(pos), ref, alt),
        )
        row = cur.fetchone()
        return str(row["variant_id"]) if row else None


# --------------------------------------------------------------------------- #
# In-memory store (tests / no-DB environments)                                #
# --------------------------------------------------------------------------- #
# Alert lifecycle transitions mirrored from ``storage.alerts`` (kept local so the
# in-memory store needs no psycopg-importing module). ``resolved``/``dismissed``
# are terminal.
_ALLOWED_TRANSITIONS = {
    "open": {"acknowledged", "in_review", "resolved", "dismissed"},
    "acknowledged": {"in_review", "resolved", "dismissed"},
    "in_review": {"acknowledged", "resolved", "dismissed"},
    "resolved": set(),
    "dismissed": set(),
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


class InMemoryClinicalStore(ClinicalStore):
    """Dependency-free store with the same semantics as :class:`DbClinicalStore`.

    Data is partitioned by ``tenant_id`` (the analogue of RLS), so a session for
    tenant B can never see tenant A's rows.
    """

    def __init__(self) -> None:
        # tenant_id -> list[receipt dict]
        self._classifications: Dict[str, List[Dict[str, Any]]] = {}
        self._alerts: Dict[str, List[Dict[str, Any]]] = {}
        self._reanalysis_events: Dict[str, List[Dict[str, Any]]] = {}
        self._reanalysis_queue: Dict[str, List[Dict[str, Any]]] = {}
        self._reanalysis_runs: Dict[str, List[Dict[str, Any]]] = {}
        self._reanalysis_policies: Dict[str, Dict[str, Any]] = {}
        self._amended_reports: Dict[str, List[Dict[str, Any]]] = {}

    # -- helpers ------------------------------------------------------------ #
    def _tenant_rows(self, table: Dict[str, List[Dict[str, Any]]], tenant_id: str):
        return table.setdefault(tenant_id, [])

    @staticmethod
    def _variant_key(chrom, pos, ref, alt, build) -> str:
        return f"{build}-{chrom}-{pos}-{ref}-{alt}"

    def _prior_for_variant(self, tenant_id: str, variant_key: str) -> Optional[Dict[str, Any]]:
        rows = [r for r in self._tenant_rows(self._classifications, tenant_id)
                if r["variant_key"] == variant_key]
        return rows[-1] if rows else None

    def _store_receipt(self, tenant_id, variant_key, classification, patient_mrn,
                       evidence=None) -> Dict[str, Any]:
        receipt = {
            "classification_id": str(uuid.uuid4()),
            "tenant_id": tenant_id,
            "patient_id": patient_mrn,
            "variant_id": variant_key,
            "variant_key": variant_key,
            "tier": classification.tier,
            "total_points": classification.total_points,
            "engine_version": classification.engine_version,
            "reconstruction_hash": classification.reconstruction_hash,
            "contributions": [_jsonable(c.__dict__) for c in classification.contributions],
            "overrides": list(classification.overrides),
            "evidence": _jsonable(evidence) if evidence is not None else None,
            "signed_off_by": None,
            "signed_off_at": None,
            "release_state": "review_pending",
            "signoff_packet": {},
            "release_scope": {},
            "config_hash": None,
            "source_snapshots": {},
            "validation_report_id": None,
            "conflict_policy_disposition": None,
            "reviewer_credential": None,
            "institutional_authorization": None,
            "effective_date": None,
            "re_review_date": None,
            "assigned_reviewer": None,
            "second_reviewer": None,
            "second_review_at": None,
            "override_rationale": None,
            "release_notes": None,
            "approved_at": None,
            "released_at": None,
            "withdrawn_at": None,
            "rereview_required_at": None,
            "created_at": _now(),
        }
        self._tenant_rows(self._classifications, tenant_id).append(receipt)
        return receipt

    # -- interface ---------------------------------------------------------- #
    def insert_classification(self, *, tenant_id, chrom, pos, ref, alt, build,
                              classification, patient_mrn=None, evidence=None):
        vk = self._variant_key(chrom, pos, ref, alt, build)
        return serialize_receipt(
            self._store_receipt(tenant_id, vk, classification, patient_mrn, evidence)
        )

    def get_classification(self, *, tenant_id, classification_id):
        for r in self._tenant_rows(self._classifications, tenant_id):
            if r["classification_id"] == classification_id:
                return serialize_receipt(r)
        return None

    def list_classifications(self, *, tenant_id, variant_key=None):
        rows = self._tenant_rows(self._classifications, tenant_id)
        if variant_key is not None:
            rows = [r for r in rows if r["variant_key"] == variant_key]
        return [serialize_receipt(r) for r in rows]

    def sign_off(self, *, tenant_id, classification_id, signed_off_by):
        for r in self._tenant_rows(self._classifications, tenant_id):
            if r["classification_id"] == classification_id:
                r["signed_off_by"] = signed_off_by
                r["signed_off_at"] = _now()
                return serialize_receipt(r)
        raise LookupError(f"classification {classification_id} not visible")

    def run_reanalysis(self, *, tenant_id, chrom, pos, ref, alt, build,
                       new_events, engine_version, trigger="evidence", patient_mrn=None):
        vk = self._variant_key(chrom, pos, ref, alt, build)
        prior = self._prior_for_variant(tenant_id, vk)
        new_clf = classify(new_events, engine_version=engine_version)

        old_tier = prior["tier"] if prior else None
        old_points = float(prior["total_points"]) if prior else None
        prior_id = prior["classification_id"] if prior else None

        # Churn guard: identical reconstruction hash -> write nothing.
        if prior is not None and new_clf.reconstruction_hash == prior["reconstruction_hash"]:
            return {
                "changed": False, "crossed": False, "old_tier": old_tier,
                "new_tier": new_clf.tier, "old_points": old_points,
                "new_points": new_clf.total_points, "new_classification_id": None,
                "reanalysis_id": None, "alert_id": None,
            }

        crossed = old_tier is not None and old_tier != new_clf.tier
        new_receipt = self._store_receipt(tenant_id, vk, new_clf, patient_mrn)
        new_id = new_receipt["classification_id"]

        if prior is None:
            return {
                "changed": True, "crossed": False, "old_tier": None,
                "new_tier": new_clf.tier, "old_points": None,
                "new_points": new_clf.total_points, "new_classification_id": new_id,
                "reanalysis_id": None, "alert_id": None,
            }

        alert_id = None
        if crossed:
            alert_id = self._insert_alert(tenant_id, vk, old_tier, new_clf.tier)
        reanalysis_id = self._record_reanalysis_event(
            tenant_id, vk, old_tier, new_clf.tier, old_points,
            new_clf.total_points, new_id, prior_id, trigger, alert_id,
        )
        return {
            "changed": True, "crossed": crossed, "old_tier": old_tier,
            "new_tier": new_clf.tier, "old_points": old_points,
            "new_points": new_clf.total_points, "new_classification_id": new_id,
            "reanalysis_id": reanalysis_id, "alert_id": alert_id,
        }

    def list_alerts(self, *, tenant_id, variant_key=None):
        rows = self._tenant_rows(self._alerts, tenant_id)
        if variant_key is not None:
            rows = [r for r in rows if r["variant_key"] == variant_key]
        return [_jsonable(r) for r in rows]

    def get_alert(self, *, tenant_id, alert_id):
        for r in self._tenant_rows(self._alerts, tenant_id):
            if r["alert_id"] == alert_id:
                return _jsonable(r)
        return None

    def update_alert_state(self, *, tenant_id, alert_id, state):
        if state not in ALERT_STATES:
            raise ValueError(f"unknown alert state {state!r}; expected one of {ALERT_STATES}")
        for r in self._tenant_rows(self._alerts, tenant_id):
            if r["alert_id"] == alert_id:
                old_state = r["state"]
                if old_state == state:
                    return _jsonable(r)
                if state not in _ALLOWED_TRANSITIONS.get(old_state, set()):
                    raise ValueError(f"illegal alert transition {old_state!r} -> {state!r}")
                r["state"] = state
                if state == "resolved":
                    r["resolved_at"] = _now()
                return _jsonable(r)
        raise LookupError(f"alert {alert_id} not visible to this session")

    def update_alert_triage(self, *, tenant_id, alert_id, triage):
        from storage.alerts import ALERT_SEVERITIES, NOTIFICATION_STATES

        if triage.get("severity") is not None and triage["severity"] not in ALERT_SEVERITIES:
            raise ValueError(
                f"unknown alert severity {triage['severity']!r}; expected one of {ALERT_SEVERITIES}"
            )
        if (
            triage.get("notification_state") is not None
            and triage["notification_state"] not in NOTIFICATION_STATES
        ):
            raise ValueError(
                "unknown notification state "
                f"{triage['notification_state']!r}; expected one of {NOTIFICATION_STATES}"
            )
        for r in self._tenant_rows(self._alerts, tenant_id):
            if r["alert_id"] == alert_id:
                mapping = {
                    "owner": "triage_owner",
                    "sla_due_at": "sla_due_at",
                    "severity": "severity",
                    "resolution_rationale": "resolution_rationale",
                    "re_review_outcome": "re_review_outcome",
                    "notification_state": "notification_state",
                }
                for src, dst in mapping.items():
                    if triage.get(src) is not None:
                        r[dst] = triage[src]
                r["triaged_at"] = _now()
                return _jsonable(r)
        raise LookupError(f"alert {alert_id} not visible to this session")

    def list_reanalysis_events(self, *, tenant_id, variant_key=None):
        rows = self._tenant_rows(self._reanalysis_events, tenant_id)
        if variant_key is not None:
            rows = [r for r in rows if r["variant_key"] == variant_key]
        return [_jsonable(r) for r in rows]

    def approve_release(self, *, tenant_id, classification_id, signoff_packet):
        from validation.signoff import APPROVED_FOR_RELEASE, SignOffPacket

        packet = SignOffPacket.from_dict(signoff_packet)
        for r in self._tenant_rows(self._classifications, tenant_id):
            if r["classification_id"] == classification_id:
                now = _now()
                r.update({
                    "signed_off_by": packet.signed_off_by,
                    "signed_off_at": now,
                    "release_state": APPROVED_FOR_RELEASE,
                    "signoff_packet": packet.to_dict(),
                    "release_scope": packet.clinical_scope.to_dict(),
                    "config_hash": packet.config_hash,
                    "source_snapshots": packet.source_snapshots,
                    "validation_report_id": packet.validation_report_id,
                    "conflict_policy_disposition": packet.conflict_policy_disposition,
                    "reviewer_credential": packet.reviewer_credential,
                    "institutional_authorization": packet.institutional_authorization,
                    "effective_date": packet.effective_date,
                    "re_review_date": packet.re_review_date,
                    "assigned_reviewer": packet.reviewer_assignment,
                    "second_reviewer": packet.second_reviewer,
                    "second_review_at": packet.second_review_at,
                    "override_rationale": packet.override_rationale,
                    "release_notes": packet.release_notes,
                    "approved_at": now,
                })
                return serialize_receipt(r)
        raise LookupError(f"classification {classification_id} not visible")

    def transition_release_state(self, *, tenant_id, classification_id, next_state, release_notes=None):
        from validation.signoff import (
            APPROVED_FOR_RELEASE,
            RELEASED,
            RE_REVIEW_REQUIRED,
            WITHDRAWN,
            transition_release_state,
        )

        for r in self._tenant_rows(self._classifications, tenant_id):
            if r["classification_id"] == classification_id:
                transition_release_state(r.get("release_state") or "review_pending", next_state)
                r["release_state"] = next_state
                if release_notes is not None:
                    r["release_notes"] = release_notes
                stamp = {
                    APPROVED_FOR_RELEASE: "approved_at",
                    RELEASED: "released_at",
                    WITHDRAWN: "withdrawn_at",
                    RE_REVIEW_REQUIRED: "rereview_required_at",
                }.get(next_state)
                if stamp:
                    r[stamp] = _now()
                return serialize_receipt(r)
        raise LookupError(f"classification {classification_id} not visible")

    def release_signoff_ledger(self, *, tenant_id, classification_id=None):
        rows = self._tenant_rows(self._classifications, tenant_id)
        out = []
        for r in rows:
            if classification_id is not None and r["classification_id"] != classification_id:
                continue
            if not r.get("signoff_packet"):
                continue
            out.append({
                "classification_id": r["classification_id"],
                "release_state": r.get("release_state"),
                "signed_off_by": r.get("signed_off_by"),
                "signed_off_at": r.get("signed_off_at"),
                "reviewer_credential": r.get("reviewer_credential"),
                "institutional_authorization": r.get("institutional_authorization"),
                "validation_report_id": r.get("validation_report_id"),
                "config_hash": r.get("config_hash"),
                "release_scope": r.get("release_scope"),
                "conflict_policy_disposition": r.get("conflict_policy_disposition"),
                "second_reviewer": r.get("second_reviewer"),
                "release_notes": r.get("release_notes"),
            })
        return [_jsonable(row) for row in out]

    def list_reanalysis_queue(self, *, tenant_id):
        return [_jsonable(row) for row in self._tenant_rows(self._reanalysis_queue, tenant_id)]

    def list_reanalysis_runs(self, *, tenant_id):
        return [_jsonable(row) for row in self._tenant_rows(self._reanalysis_runs, tenant_id)]

    def get_reanalysis_policy(self, *, tenant_id):
        from ops.scheduler import TenantReanalysisPolicy

        return _jsonable(self._reanalysis_policies.get(tenant_id) or {
            "tenant_id": tenant_id,
            **TenantReanalysisPolicy().to_dict(),
        })

    def set_reanalysis_policy(self, *, tenant_id, policy):
        from ops.scheduler import TenantReanalysisPolicy

        row = {
            "policy_id": str(uuid.uuid4()),
            "tenant_id": tenant_id,
            **TenantReanalysisPolicy.from_dict(policy).to_dict(),
            "updated_at": _now(),
        }
        self._reanalysis_policies[tenant_id] = row
        return _jsonable(row)

    def record_amended_report(self, *, tenant_id, classification_id, lifecycle):
        report = {
            "amended_report_id": str(uuid.uuid4()),
            "tenant_id": tenant_id,
            "classification_id": classification_id,
            **lifecycle["report"],
            "created_at": _now(),
        }
        notifications = []
        for notification in lifecycle.get("notifications", []):
            notifications.append({
                "notification_id": str(uuid.uuid4()),
                "tenant_id": tenant_id,
                **notification,
                "created_at": _now(),
            })
        record = {
            "report": report,
            "notifications": notifications,
            "payload": lifecycle["payload"],
        }
        self._tenant_rows(self._amended_reports, tenant_id).append(record)
        return _jsonable(record)

    # -- internal write helpers --------------------------------------------- #
    def _insert_alert(self, tenant_id, variant_key, old_tier, new_tier) -> str:
        alert = {
            "alert_id": str(uuid.uuid4()),
            "tenant_id": tenant_id,
            "variant_id": variant_key,
            "variant_key": variant_key,
            "old_tier": old_tier,
            "new_tier": new_tier,
            "serious": is_serious_crossing(old_tier, new_tier),
            "state": "open",
            "triage_owner": None,
            "sla_due_at": None,
            "severity": "critical" if is_serious_crossing(old_tier, new_tier) else "standard",
            "resolution_rationale": None,
            "re_review_outcome": None,
            "notification_state": "pending",
            "triaged_at": None,
            "resolved_at": None,
            "created_at": _now(),
        }
        self._tenant_rows(self._alerts, tenant_id).append(alert)
        return alert["alert_id"]

    def _record_reanalysis_event(self, tenant_id, variant_key, old_tier, new_tier,
                                 old_points, new_points, new_id, prior_id, trigger,
                                 alert_id) -> str:
        event = {
            "reanalysis_id": str(uuid.uuid4()),
            "tenant_id": tenant_id,
            "variant_id": variant_key,
            "variant_key": variant_key,
            "prior_classification_id": prior_id,
            "new_classification_id": new_id,
            "old_tier": old_tier,
            "new_tier": new_tier,
            "old_points": old_points,
            "new_points": new_points,
            "trigger": trigger,
            "crossed": old_tier != new_tier,
            "alert_id": alert_id,
            "created_at": _now(),
        }
        self._tenant_rows(self._reanalysis_events, tenant_id).append(event)
        return event["reanalysis_id"]
