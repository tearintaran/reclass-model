"""Clinical classification *receipts* (the system of record for a result).

A receipt is an auditable snapshot of one engine run for a (tenant, patient,
variant): the resulting tier and total points, the per-criterion contribution
breakdown, any stand-alone overrides, the engine version, the SHA-256
reconstruction hash, and the human sign-off state. ``clinical.classification`` is
RLS-protected, so every function here is meant to run on a cursor obtained from
``storage.db.tenant_session``.
"""
from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any, Dict, List, Optional

from psycopg.types.json import Jsonb

from validation.signoff import (
    APPROVED_FOR_RELEASE,
    RELEASED,
    RE_REVIEW_REQUIRED,
    REVIEW_PENDING,
    WITHDRAWN,
    RELEASE_STATES,
    SignOffPacket,
    transition_release_state,
)

_VARIANT_KEY_SQL = "(v.build || '-' || v.chrom || '-' || v.pos::text || '-' || v.ref || '-' || v.alt)"


def variant_key(chrom: str, pos: int, ref: str, alt: str, build: str = "GRCh38") -> str:
    """Canonical de-identified variant key, e.g. ``GRCh38-1-100-A-G``.

    This is the *only* link between the clinical and research domains, and it is
    reconstructed from public coordinates — there is no shared surrogate key, so
    the database cannot join research rows back to a patient.
    """
    return f"{build}-{chrom}-{pos}-{ref}-{alt}"


def insert_tenant(cur, name: str) -> str:
    """Insert a tenant (not RLS-protected) and return its id."""
    cur.execute(
        "INSERT INTO clinical.tenant (name) VALUES (%s) RETURNING tenant_id",
        (name,),
    )
    return str(cur.fetchone()["tenant_id"])


def insert_patient(cur, *, tenant_id: str, mrn: str) -> str:
    """Insert a patient for ``tenant_id``; requires a tenant-scoped session."""
    cur.execute(
        "INSERT INTO clinical.patient (tenant_id, mrn) VALUES (%s, %s) "
        "RETURNING patient_id",
        (tenant_id, mrn),
    )
    return str(cur.fetchone()["patient_id"])


def upsert_variant(cur, *, chrom: str, pos: int, ref: str, alt: str,
                   build: str = "GRCh38") -> str:
    """Insert (or fetch) a clinical variant row and return its id."""
    cur.execute(
        "INSERT INTO clinical.variant (chrom, pos, ref, alt, build) "
        "VALUES (%s, %s, %s, %s, %s) "
        "ON CONFLICT (build, chrom, pos, ref, alt) DO NOTHING",
        (chrom, pos, ref, alt, build),
    )
    cur.execute(
        "SELECT variant_id FROM clinical.variant "
        "WHERE build = %s AND chrom = %s AND pos = %s AND ref = %s AND alt = %s",
        (build, chrom, pos, ref, alt),
    )
    return str(cur.fetchone()["variant_id"])


def _contributions_to_json(contributions: Any) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for c in contributions:
        rows.append(asdict(c) if is_dataclass(c) else dict(c))  # type: ignore[arg-type]
    return rows


def insert_classification(
    cur,
    *,
    tenant_id: str,
    variant_id: str,
    classification,
    patient_id: Optional[str] = None,
    evidence: Optional[Dict[str, Any]] = None,
    signed_off_by: Optional[str] = None,
    signed_off_at=None,
) -> str:
    """Persist a classification receipt produced by ``engine.scoring.classify``.

    ``classification`` is an ``engine.scoring.Classification`` (or any object with
    the same attributes). ``evidence`` is the resolved ``EvidenceBundle.to_dict()``
    (transcript identity + PS4 cohort counts + provenance) when the result came
    from evidence resolution; it is stored verbatim so reviewer/FHIR reports can
    surface those fields, and is ``None`` for results scored from direct events.
    Returns the new ``classification_id``. Must run inside a tenant-scoped session
    matching ``tenant_id`` (RLS ``WITH CHECK``).
    """
    contributions = _contributions_to_json(classification.contributions)
    overrides = list(classification.overrides)
    cur.execute(
        """
        INSERT INTO clinical.classification (
            tenant_id, patient_id, variant_id, tier, total_points,
            engine_version, reconstruction_hash, contributions, overrides,
            evidence, signed_off_by, signed_off_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING classification_id
        """,
        (
            tenant_id,
            patient_id,
            variant_id,
            classification.tier,
            classification.total_points,
            classification.engine_version,
            classification.reconstruction_hash,
            Jsonb(contributions),
            Jsonb(overrides),
            Jsonb(evidence) if evidence is not None else None,
            signed_off_by,
            signed_off_at,
        ),
    )
    return str(cur.fetchone()["classification_id"])


def get_classification(cur, classification_id: str) -> Optional[Dict[str, Any]]:
    """Read a classification receipt back (subject to RLS for this session)."""
    cur.execute(
        """
        SELECT c.classification_id, c.tenant_id, c.patient_id, c.variant_id,
               """ + _VARIANT_KEY_SQL + """ AS variant_key,
               c.tier, c.total_points, c.engine_version, c.reconstruction_hash,
               c.contributions, c.overrides, c.evidence, c.signed_off_by,
               c.signed_off_at, c.created_at,
               c.release_state, c.signoff_packet, c.release_scope,
               c.config_hash, c.source_snapshots, c.validation_report_id,
               c.conflict_policy_disposition, c.reviewer_credential,
               c.institutional_authorization, c.effective_date, c.re_review_date,
               c.assigned_reviewer, c.second_reviewer, c.second_review_at,
               c.override_rationale, c.release_notes, c.approved_at,
               c.released_at, c.withdrawn_at, c.rereview_required_at
          FROM clinical.classification c
          JOIN clinical.variant v ON v.variant_id = c.variant_id
         WHERE c.classification_id = %s
        """,
        (classification_id,),
    )
    return cur.fetchone()


def list_classifications(cur, *, variant_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """List receipts visible to the current session, optionally by variant."""
    if variant_id is None:
        cur.execute(
            "SELECT c.*, " + _VARIANT_KEY_SQL + " AS variant_key "
            "FROM clinical.classification c "
            "JOIN clinical.variant v ON v.variant_id = c.variant_id "
            "ORDER BY c.created_at"
        )
    else:
        cur.execute(
            "SELECT c.*, " + _VARIANT_KEY_SQL + " AS variant_key "
            "FROM clinical.classification c "
            "JOIN clinical.variant v ON v.variant_id = c.variant_id "
            "WHERE c.variant_id = %s ORDER BY c.created_at",
            (variant_id,),
        )
    return cur.fetchall()


def sign_off(cur, classification_id: str, *, signed_off_by: str) -> None:
    """Record a credentialed human sign-off (sets signer + timestamp)."""
    cur.execute(
        "UPDATE clinical.classification "
        "SET signed_off_by = %s, signed_off_at = now() "
        "WHERE classification_id = %s",
        (signed_off_by, classification_id),
    )


def record_release_signoff(
    cur,
    classification_id: str,
    *,
    signoff_packet: Dict[str, Any],
) -> Dict[str, Any]:
    """Persist a full release-gate sign-off packet and approve the receipt.

    The gate evaluation itself lives in :mod:`validation.release_gate`; this helper
    records the already-approved packet as structured receipt state. It stamps the
    legacy ``signed_off_by`` fields too so existing report surfaces continue to
    render a signed result.
    """
    packet = SignOffPacket.from_dict(signoff_packet)
    if packet.signed_off_by in (None, ""):
        raise ValueError("signoff_packet.signed_off_by is required")
    cur.execute(
        """
        UPDATE clinical.classification
           SET signed_off_by = %s,
               signed_off_at = now(),
               release_state = %s,
               signoff_packet = %s,
               release_scope = %s,
               config_hash = %s,
               source_snapshots = %s,
               validation_report_id = %s,
               conflict_policy_disposition = %s,
               reviewer_credential = %s,
               institutional_authorization = %s,
               effective_date = %s,
               re_review_date = %s,
               assigned_reviewer = %s,
               second_reviewer = %s,
               second_review_at = %s,
               override_rationale = %s,
               release_notes = %s,
               approved_at = now()
         WHERE classification_id = %s
        RETURNING classification_id
        """,
        (
            packet.signed_off_by,
            APPROVED_FOR_RELEASE,
            Jsonb(packet.to_dict()),
            Jsonb(packet.clinical_scope.to_dict()),
            packet.config_hash,
            Jsonb(packet.source_snapshots),
            packet.validation_report_id,
            packet.conflict_policy_disposition,
            packet.reviewer_credential,
            packet.institutional_authorization,
            packet.effective_date,
            packet.re_review_date,
            packet.reviewer_assignment,
            packet.second_reviewer,
            packet.second_review_at,
            packet.override_rationale,
            packet.release_notes,
            classification_id,
        ),
    )
    if cur.fetchone() is None:
        raise LookupError(f"classification {classification_id} not visible to this session")
    # webhook-seam: emit classification.approved_for_release with packet id/scope.
    row = get_classification(cur, classification_id)
    if row is None:
        raise LookupError(f"classification {classification_id} not visible to this session")
    return row


def update_release_state(
    cur,
    classification_id: str,
    *,
    next_state: str,
    release_notes: Optional[str] = None,
) -> Dict[str, Any]:
    """Transition a receipt through the release-state machine."""
    row = get_classification(cur, classification_id)
    if row is None:
        raise LookupError(f"classification {classification_id} not visible to this session")
    current = row.get("release_state") or REVIEW_PENDING
    transition_release_state(current, next_state)

    timestamp_column = {
        APPROVED_FOR_RELEASE: "approved_at",
        RELEASED: "released_at",
        WITHDRAWN: "withdrawn_at",
        RE_REVIEW_REQUIRED: "rereview_required_at",
    }.get(next_state)
    set_timestamp = f", {timestamp_column} = now()" if timestamp_column else ""
    cur.execute(
        f"""
        UPDATE clinical.classification
           SET release_state = %s,
               release_notes = COALESCE(%s, release_notes)
               {set_timestamp}
         WHERE classification_id = %s
        RETURNING classification_id
        """,
        (next_state, release_notes, classification_id),
    )
    if cur.fetchone() is None:
        raise LookupError(f"classification {classification_id} not visible to this session")
    # webhook-seam: emit classification.release_state_changed.
    updated = get_classification(cur, classification_id)
    if updated is None:
        raise LookupError(f"classification {classification_id} not visible to this session")
    return updated


def release_signoff_ledger(cur, *, classification_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Return structured sign-off ledger rows visible to this tenant session."""
    params: List[Any] = []
    where = "WHERE c.signoff_packet <> '{}'::jsonb"
    if classification_id is not None:
        where += " AND c.classification_id = %s"
        params.append(classification_id)
    cur.execute(
        """
        SELECT c.classification_id, c.release_state, c.signed_off_by, c.signed_off_at,
               c.reviewer_credential, c.institutional_authorization,
               c.validation_report_id, c.config_hash, c.release_scope,
               c.conflict_policy_disposition, c.second_reviewer, c.release_notes
          FROM clinical.classification c
        """
        + where
        + " ORDER BY c.signed_off_at NULLS LAST, c.created_at",
        params,
    )
    return cur.fetchall()


def known_release_states() -> tuple[str, ...]:
    """Expose release states to tests and migration checks without importing validation."""
    return RELEASE_STATES
