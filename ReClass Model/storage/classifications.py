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
        SELECT classification_id, tenant_id, patient_id, variant_id, tier,
               total_points, engine_version, reconstruction_hash, contributions,
               overrides, evidence, signed_off_by, signed_off_at, created_at
          FROM clinical.classification
         WHERE classification_id = %s
        """,
        (classification_id,),
    )
    return cur.fetchone()


def list_classifications(cur, *, variant_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """List receipts visible to the current session, optionally by variant."""
    if variant_id is None:
        cur.execute(
            "SELECT * FROM clinical.classification ORDER BY created_at"
        )
    else:
        cur.execute(
            "SELECT * FROM clinical.classification WHERE variant_id = %s "
            "ORDER BY created_at",
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
