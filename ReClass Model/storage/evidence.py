"""De-identified evidence store (``research.evidence_events``).

The research domain carries **no** patient or tenant identifiers and no surrogate
key into the clinical schema — only the public ``variant_key`` coordinate. These
rows are the standardized evidence that the engine sums into a tier, and they are
what ``storage.verify`` replays to prove a stored classification reconstructs.

Evidence is stored *faithfully*: an event whose contribution was derived from a
named strength (``applied_strength``) keeps ``points = NULL`` rather than its
resolved magnitude, so re-running the engine reproduces the exact
``reconstruction_hash`` byte-for-byte.
"""
from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional, Tuple

from psycopg.types.json import Jsonb

from engine.scoring import EvidenceEvent
from evidence.model import EvidenceBundle


def upsert_research_variant(cur, *, variant_key: str, chrom: str, pos: int,
                            ref: str, alt: str) -> None:
    """Insert (idempotently) the de-identified variant row."""
    cur.execute(
        "INSERT INTO research.variant (variant_key, chrom, pos, ref, alt) "
        "VALUES (%s, %s, %s, %s, %s) "
        "ON CONFLICT (variant_key) DO NOTHING",
        (variant_key, chrom, pos, ref, alt),
    )


def parse_public_variant_key(variant_key: str) -> Optional[Tuple[str, int, str, str]]:
    """Parse ``GRCh38-chrom-pos-ref-alt`` or provider ``chrom-pos-ref-alt`` keys.

    Evidence providers may use the upstream lookup id without a genome-build
    prefix, while the clinical storage layer derives ``GRCh38-...`` keys. Both are
    public coordinate keys, so storage accepts either shape and stores the row
    under the exact key supplied by the bundle.
    """
    parts = str(variant_key).split("-")
    if len(parts) == 5:
        _build, chrom, pos, ref, alt = parts
    elif len(parts) == 4:
        chrom, pos, ref, alt = parts
    else:
        return None
    try:
        return chrom, int(pos), ref, alt
    except ValueError:
        return None


def insert_evidence_event(cur, *, variant_key: str, event: EvidenceEvent,
                          bundle_id: Optional[str] = None,
                          ordinal: Optional[int] = None) -> str:
    """Persist one standardized evidence event, preserving original fields.

    ``points`` is stored exactly as the engine event carries it (``None`` for
    strength-derived events) so the reconstruction hash is reproducible.
    ``bundle_id`` / ``ordinal`` (optional, additive) tie the event to the
    :class:`~evidence.model.EvidenceBundle` it came from and preserve its position.
    """
    cur.execute(
        """
        INSERT INTO research.evidence_events (
            variant_key, source, acmg_criterion, direction, applied_strength,
            points, source_version, bundle_id, ordinal
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING evidence_id
        """,
        (
            variant_key,
            event.source,
            event.acmg_criterion,
            event.evidence_direction,
            event.applied_strength,
            event.points,
            event.source_version,
            bundle_id,
            ordinal,
        ),
    )
    return str(cur.fetchone()["evidence_id"])


def insert_evidence_events(cur, *, variant_key: str,
                           events: List[EvidenceEvent],
                           bundle_id: Optional[str] = None) -> List[str]:
    return [
        insert_evidence_event(cur, variant_key=variant_key, event=e,
                              bundle_id=bundle_id,
                              ordinal=(i if bundle_id is not None else None))
        for i, e in enumerate(events)
    ]


def get_evidence_rows(cur, variant_key: str) -> List[Dict[str, Any]]:
    """Raw ``research.evidence_events`` rows for a variant key."""
    cur.execute(
        """
        SELECT evidence_id, variant_key, source, acmg_criterion, direction,
               applied_strength, points, source_version, observed_at
          FROM research.evidence_events
         WHERE variant_key = %s
         ORDER BY observed_at, evidence_id
        """,
        (variant_key,),
    )
    return cur.fetchall()


def _row_to_event(row: Dict[str, Any]) -> EvidenceEvent:
    points: Optional[float] = (
        None if row["points"] is None else float(row["points"])
    )
    return EvidenceEvent(
        source=row["source"],
        acmg_criterion=row["acmg_criterion"],
        evidence_direction=row["direction"],
        applied_strength=row["applied_strength"],
        points=points,
        source_version=row["source_version"],
    )


def get_evidence_events(cur, variant_key: str) -> List[EvidenceEvent]:
    """Reconstruct engine ``EvidenceEvent`` objects from stored rows."""
    return [_row_to_event(row) for row in get_evidence_rows(cur, variant_key)]


# --------------------------------------------------------------------------- #
# Persisted EvidenceBundle provenance (gap §3)                                #
# --------------------------------------------------------------------------- #
def insert_evidence_bundle(cur, bundle: EvidenceBundle, *,
                           engine_version: Optional[str] = None) -> str:
    """Persist a full :class:`~evidence.model.EvidenceBundle` (provenance + events).

    Writes the de-identified ``research.variant`` row (if coordinates are present
    on the bundle's ``variant_key``), the ``research.evidence_bundle`` provenance
    row, its ``research.source_record`` children, and the scored
    ``research.evidence_events`` linked back to the bundle. Returns the new
    ``bundle_id``.

    The bundle carries no patient/tenant identifier; nothing here introduces one,
    keeping the research/clinical boundary intact.
    """
    if bundle.variant_key is None:
        raise ValueError("EvidenceBundle.variant_key is required to persist a bundle")

    bundle_dict = bundle.to_dict()
    parsed = parse_public_variant_key(bundle.variant_key)
    if parsed is not None:
        chrom, pos, ref, alt = parsed
        upsert_research_variant(
            cur, variant_key=bundle.variant_key,
            chrom=chrom, pos=pos, ref=ref, alt=alt,
        )

    if engine_version is None:
        bundle_hash = bundle.reconstruction_hash()
    else:
        bundle_hash = bundle.reconstruction_hash(engine_version)

    cur.execute(
        """
        INSERT INTO research.evidence_bundle (
            variant_key, schema_version, bundle_hash, provider_versions,
            warnings, match
        ) VALUES (%s, %s, %s, %s, %s, %s)
        RETURNING bundle_id
        """,
        (
            bundle.variant_key,
            bundle_dict["schema_version"],
            bundle_hash,
            Jsonb(bundle_dict["provider_versions"]),
            Jsonb(bundle_dict["warnings"]),
            Jsonb(bundle_dict["match"]) if bundle.match is not None else None,
        ),
    )
    bundle_id = str(cur.fetchone()["bundle_id"])

    for ordinal, record in enumerate(bundle.source_records):
        insert_source_record(cur, bundle_id=bundle_id, ordinal=ordinal,
                             record=record)

    insert_evidence_events(cur, variant_key=bundle.variant_key,
                           events=bundle.events, bundle_id=bundle_id)
    return bundle_id


def insert_source_record(cur, *, bundle_id: str, ordinal: int,
                         record: Dict[str, Any]) -> str:
    """Persist one raw matched record for a bundle.

    The compact reference (``payload_ref``/``source``) is always retained; the raw
    ``payload`` is a prunable cache (see the schema retention comment). ``record``
    keys ``source``/``payload_ref`` are lifted into columns; the whole record is
    kept verbatim as ``payload`` so the bundle round-trips losslessly.
    """
    source = record.get("source")
    payload_ref = record.get("payload_ref") or record.get("ref")
    cur.execute(
        """
        INSERT INTO research.source_record (bundle_id, ordinal, source, payload_ref, payload)
        VALUES (%s, %s, %s, %s, %s)
        RETURNING source_record_id
        """,
        (bundle_id, ordinal, source, payload_ref, Jsonb(record)),
    )
    return str(cur.fetchone()["source_record_id"])


def get_bundle_row(cur, bundle_id: str) -> Optional[Dict[str, Any]]:
    """Raw ``research.evidence_bundle`` provenance row."""
    cur.execute(
        """
        SELECT bundle_id, variant_key, schema_version, bundle_hash,
               provider_versions, warnings, match, created_at
          FROM research.evidence_bundle
         WHERE bundle_id = %s
        """,
        (bundle_id,),
    )
    return cur.fetchone()


def get_source_records(cur, bundle_id: str) -> List[Dict[str, Any]]:
    """Source records for a bundle, in stored order.

    Returns the raw ``payload`` when retained; once a payload has been pruned
    (retention policy) a compact ``{source, payload_ref}`` reference is returned so
    the bundle still reconstructs (without the bulky raw record).
    """
    cur.execute(
        """
        SELECT source, payload_ref, payload
          FROM research.source_record
         WHERE bundle_id = %s
         ORDER BY ordinal
        """,
        (bundle_id,),
    )
    records: List[Dict[str, Any]] = []
    for row in cur.fetchall():
        if row["payload"] is not None:
            records.append(dict(row["payload"]))
        else:
            records.append({"source": row["source"], "payload_ref": row["payload_ref"]})
    return records


def get_bundle_events(cur, bundle_id: str) -> List[EvidenceEvent]:
    """Reconstruct the exact ``EvidenceEvent`` list persisted for one bundle.

    Ordered by the stored ``ordinal`` so the bundle round-trips in its original
    event order (falling back to insertion order for any legacy NULL ordinals).
    """
    cur.execute(
        """
        SELECT evidence_id, variant_key, source, acmg_criterion, direction,
               applied_strength, points, source_version, observed_at, ordinal
          FROM research.evidence_events
         WHERE bundle_id = %s
         ORDER BY ordinal NULLS LAST, observed_at, evidence_id
        """,
        (bundle_id,),
    )
    return [_row_to_event(row) for row in cur.fetchall()]


def get_evidence_bundle(cur, bundle_id: str) -> Optional[EvidenceBundle]:
    """Reconstruct a full :class:`~evidence.model.EvidenceBundle` from storage."""
    row = get_bundle_row(cur, bundle_id)
    if row is None:
        return None
    match = row["match"]
    return EvidenceBundle(
        variant_key=row["variant_key"],
        events=get_bundle_events(cur, bundle_id),
        provider_versions=dict(row["provider_versions"] or {}),
        source_records=[dict(r) for r in get_source_records(cur, bundle_id)],
        warnings=list(row["warnings"] or []),
        match=dict(match) if match is not None else None,
    )


def get_bundles_for_variant(cur, variant_key: str) -> List[Dict[str, Any]]:
    """All persisted bundle provenance rows for a variant key (newest last)."""
    cur.execute(
        """
        SELECT bundle_id, variant_key, schema_version, bundle_hash,
               provider_versions, warnings, match, created_at
          FROM research.evidence_bundle
         WHERE variant_key = %s
         ORDER BY created_at, bundle_id
        """,
        (variant_key,),
    )
    return cur.fetchall()


def prune_raw_payloads(cur, bundle_id: str) -> int:
    """Drop the prunable raw ``payload`` cache for a bundle (retention policy).

    Compact provenance (``payload_ref``/``source``/``provider_versions``) and the
    scored events are kept, so this never affects reconstruction or audit. Returns
    the number of source records pruned.
    """
    cur.execute(
        "UPDATE research.source_record SET payload = NULL "
        "WHERE bundle_id = %s AND payload IS NOT NULL",
        (bundle_id,),
    )
    return cur.rowcount


# --------------------------------------------------------------------------- #
# Cohort counts (de-identified) for PS4 (gap §8)                              #
# --------------------------------------------------------------------------- #
def upsert_cohort_count(cur, *, variant_key: str, ancestry: str,
                        case_count: int, control_count: int) -> None:
    """Idempotently store a de-identified cohort case/control count.

    Carries no patient identifiers -- only aggregate counts per ancestry for the
    public ``variant_key``. Re-ingesting the same (variant_key, ancestry) replaces
    the counts (sources publish refreshed aggregates over time).
    """
    cur.execute(
        """
        INSERT INTO research.cohort_counts (variant_key, ancestry, case_count, control_count)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (variant_key, ancestry) DO UPDATE
            SET case_count = EXCLUDED.case_count,
                control_count = EXCLUDED.control_count
        """,
        (variant_key, ancestry, case_count, control_count),
    )


def get_cohort_counts(cur, variant_key: str) -> List[Dict[str, Any]]:
    """All cohort count rows for a variant key, ordered by ancestry."""
    cur.execute(
        "SELECT variant_key, ancestry, case_count, control_count "
        "FROM research.cohort_counts WHERE variant_key = %s ORDER BY ancestry",
        (variant_key,),
    )
    return cur.fetchall()


# --------------------------------------------------------------------------- #
# Reviewer/pipeline-entered evidence (job1 task 1) -- de-identified, research  #
# --------------------------------------------------------------------------- #
def _jsonify(value: Any) -> Any:
    """Coerce uuid/date/datetime values to JSON-friendly strings (recursively)."""
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        return [_jsonify(v) for v in value]
    if isinstance(value, dict):
        return {k: _jsonify(v) for k, v in value.items()}
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    return value


_REVIEWER_EVIDENCE_COLUMNS = (
    "reviewer_evidence_id, variant_key, acmg_criterion, direction, applied_strength, "
    "points, source, source_version, source_url, checksum, checksum_algorithm, "
    "access_date, reviewer, reviewer_credential, status, notes, entered_at, "
    "expires_at, re_review_at"
)


def _reviewer_evidence_row(row: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    out = {k: _jsonify(v) for k, v in dict(row).items()}
    out["evidence_direction"] = out.get("direction")
    out["points"] = None if out.get("points") is None else float(out["points"])
    out["is_expired"] = out.get("status") == "expired"
    return out


def insert_reviewer_evidence(cur, evidence: Any) -> Dict[str, Any]:
    """Persist one reviewer-entered :class:`evidence.workbench.ReviewerEvidence`.

    The de-identified ``research.variant`` row is upserted first (FK target); the
    research/clinical boundary stays intact (no patient/tenant identifier is written).
    Returns the stored row with the assigned id and server-set ``entered_at``.
    """
    parsed = parse_public_variant_key(evidence.variant_key)
    if parsed is None:
        raise ValueError(
            f"reviewer evidence variant_key {evidence.variant_key!r} is not a coordinate key"
        )
    chrom, pos, ref, alt = parsed
    upsert_research_variant(cur, variant_key=evidence.variant_key,
                            chrom=chrom, pos=pos, ref=ref, alt=alt)
    cur.execute(
        f"""
        INSERT INTO research.reviewer_evidence (
            variant_key, acmg_criterion, direction, applied_strength, points,
            source, source_version, source_url, checksum, checksum_algorithm,
            access_date, reviewer, reviewer_credential, status, notes,
            expires_at, re_review_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING {_REVIEWER_EVIDENCE_COLUMNS}
        """,
        (
            evidence.variant_key, evidence.acmg_criterion, evidence.evidence_direction,
            evidence.applied_strength, evidence.points, evidence.source,
            evidence.source_version, evidence.source_url, evidence.checksum,
            evidence.checksum_algorithm, evidence.access_date, evidence.reviewer,
            evidence.reviewer_credential, evidence.status, evidence.notes,
            evidence.expires_at, evidence.re_review_at,
        ),
    )
    row = _reviewer_evidence_row(cur.fetchone())
    assert row is not None
    return row


def list_reviewer_evidence(cur, *, variant_key: Optional[str] = None,
                           status: Optional[str] = None) -> List[Dict[str, Any]]:
    """Reviewer-entered evidence rows, optionally filtered by variant/status."""
    clauses: List[str] = []
    params: List[Any] = []
    if variant_key is not None:
        clauses.append("variant_key = %s")
        params.append(variant_key)
    if status is not None:
        clauses.append("status = %s")
        params.append(status)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    cur.execute(
        f"SELECT {_REVIEWER_EVIDENCE_COLUMNS} FROM research.reviewer_evidence"
        f"{where} ORDER BY variant_key, entered_at, reviewer_evidence_id",
        tuple(params),
    )
    return [r for r in (_reviewer_evidence_row(row) for row in cur.fetchall()) if r is not None]


def get_reviewer_evidence(cur, reviewer_evidence_id: str) -> Optional[Dict[str, Any]]:
    cur.execute(
        f"SELECT {_REVIEWER_EVIDENCE_COLUMNS} FROM research.reviewer_evidence "
        "WHERE reviewer_evidence_id = %s",
        (reviewer_evidence_id,),
    )
    return _reviewer_evidence_row(cur.fetchone())


def set_reviewer_evidence_status(cur, reviewer_evidence_id: str,
                                 status: str) -> Dict[str, Any]:
    cur.execute(
        f"UPDATE research.reviewer_evidence SET status = %s "
        f"WHERE reviewer_evidence_id = %s RETURNING {_REVIEWER_EVIDENCE_COLUMNS}",
        (status, reviewer_evidence_id),
    )
    row = _reviewer_evidence_row(cur.fetchone())
    if row is None:
        raise LookupError(f"reviewer evidence {reviewer_evidence_id} not found")
    return row


def expire_reviewer_evidence(cur, *, as_of: Optional[str] = None) -> List[str]:
    """Flip active entries past ``expires_at`` (default now) to ``expired``."""
    cutoff = as_of if as_of is not None else "now()"
    if as_of is not None:
        cur.execute(
            "UPDATE research.reviewer_evidence SET status = 'expired' "
            "WHERE status = 'active' AND expires_at IS NOT NULL AND expires_at <= %s "
            "RETURNING reviewer_evidence_id",
            (cutoff,),
        )
    else:
        cur.execute(
            "UPDATE research.reviewer_evidence SET status = 'expired' "
            "WHERE status = 'active' AND expires_at IS NOT NULL AND expires_at <= now() "
            "RETURNING reviewer_evidence_id"
        )
    return [str(r["reviewer_evidence_id"]) for r in cur.fetchall()]


# --------------------------------------------------------------------------- #
# Evidence coverage + curation queue (job1 tasks 2-3) -- tenant-scoped (RLS)   #
# --------------------------------------------------------------------------- #
def _coverage_field(record: Any, name: str) -> Any:
    if isinstance(record, dict):
        return record.get(name)
    return getattr(record, name, None)


def upsert_coverage(cur, *, tenant_id: str, record: Any) -> Dict[str, Any]:
    """Idempotently store/refresh a (tenant, variant) coverage row.

    ``record`` is an ``evidence.coverage.CoverageRecord`` or an equivalent dict. The
    cursor must come from a tenant-scoped session so RLS applies.
    """
    cur.execute(
        """
        INSERT INTO clinical.evidence_coverage (
            tenant_id, variant_key, gene, vcep, disease, variant_class, provider,
            present_criteria, missing_criteria, blocked, blocking_reason, updated_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
        ON CONFLICT (tenant_id, variant_key) DO UPDATE SET
            gene = EXCLUDED.gene, vcep = EXCLUDED.vcep, disease = EXCLUDED.disease,
            variant_class = EXCLUDED.variant_class, provider = EXCLUDED.provider,
            present_criteria = EXCLUDED.present_criteria,
            missing_criteria = EXCLUDED.missing_criteria,
            blocked = EXCLUDED.blocked, blocking_reason = EXCLUDED.blocking_reason,
            updated_at = now()
        RETURNING coverage_id, tenant_id, variant_key, gene, vcep, disease,
                  variant_class, provider, present_criteria, missing_criteria,
                  blocked, blocking_reason, updated_at
        """,
        (
            tenant_id, _coverage_field(record, "variant_key"),
            _coverage_field(record, "gene"), _coverage_field(record, "vcep"),
            _coverage_field(record, "disease"), _coverage_field(record, "variant_class"),
            _coverage_field(record, "provider"),
            Jsonb(list(_coverage_field(record, "present_criteria") or [])),
            Jsonb(list(_coverage_field(record, "missing_categories")
                       or _coverage_field(record, "missing_criteria") or [])),
            bool(_coverage_field(record, "blocked")),
            _coverage_field(record, "blocking_reason"),
        ),
    )
    row = cur.fetchone()
    return {k: _jsonify(v) for k, v in dict(row).items()}


def list_coverage(cur) -> List[Dict[str, Any]]:
    """All coverage rows visible to the tenant session (RLS-scoped)."""
    cur.execute(
        "SELECT coverage_id, tenant_id, variant_key, gene, vcep, disease, "
        "variant_class, provider, present_criteria, missing_criteria, blocked, "
        "blocking_reason, updated_at FROM clinical.evidence_coverage "
        "ORDER BY variant_key"
    )
    return [{k: _jsonify(v) for k, v in dict(row).items()} for row in cur.fetchall()]


def enqueue_curation_item(cur, *, tenant_id: str, item: Any) -> Optional[Dict[str, Any]]:
    """Enqueue a curation item; a duplicate OPEN (tenant, variant, kind) is a no-op.

    ``item`` is an ``evidence.curation.CurationItem`` or an equivalent dict. Returns
    the inserted row, or ``None`` when the open item already existed.
    """
    detail = item.detail if hasattr(item, "detail") else item.get("detail")
    cur.execute(
        """
        INSERT INTO clinical.curation_queue (tenant_id, variant_key, kind, severity, detail)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (tenant_id, variant_key, kind) WHERE state = 'open' DO NOTHING
        RETURNING curation_id, tenant_id, variant_key, kind, severity, detail,
                  state, created_at, resolved_at
        """,
        (
            tenant_id,
            item.variant_key if hasattr(item, "variant_key") else item.get("variant_key"),
            item.kind if hasattr(item, "kind") else item.get("kind"),
            item.severity if hasattr(item, "severity") else item.get("severity", "warning"),
            Jsonb(dict(detail or {})),
        ),
    )
    row = cur.fetchone()
    return {k: _jsonify(v) for k, v in dict(row).items()} if row is not None else None


def list_curation_items(cur, *, kind: Optional[str] = None,
                        state: Optional[str] = None) -> List[Dict[str, Any]]:
    """Curation queue items visible to the tenant session, newest first."""
    clauses: List[str] = []
    params: List[Any] = []
    if kind is not None:
        clauses.append("kind = %s")
        params.append(kind)
    if state is not None:
        clauses.append("state = %s")
        params.append(state)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    cur.execute(
        "SELECT curation_id, tenant_id, variant_key, kind, severity, detail, state, "
        f"created_at, resolved_at FROM clinical.curation_queue{where} "
        "ORDER BY created_at DESC, curation_id",
        tuple(params),
    )
    return [{k: _jsonify(v) for k, v in dict(row).items()} for row in cur.fetchall()]


def set_curation_state(cur, curation_id: str, state: str) -> Dict[str, Any]:
    """Update a curation item's state; stamps ``resolved_at`` on resolution."""
    cur.execute(
        "UPDATE clinical.curation_queue SET state = %s, "
        "resolved_at = CASE WHEN %s IN ('resolved', 'dismissed') THEN now() ELSE resolved_at END "
        "WHERE curation_id = %s "
        "RETURNING curation_id, tenant_id, variant_key, kind, severity, detail, "
        "state, created_at, resolved_at",
        (state, state, curation_id),
    )
    row = cur.fetchone()
    if row is None:
        raise LookupError(f"curation item {curation_id} not visible to this session")
    return {k: _jsonify(v) for k, v in dict(row).items()}
