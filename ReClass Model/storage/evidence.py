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
