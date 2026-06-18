"""Reconstruction verifier.

Proves that a stored classification receipt can be re-derived byte-for-byte from
its persisted evidence. We read the de-identified ``research.evidence_events`` for
the receipt's variant, replay ``engine.scoring.classify`` at the receipt's
recorded ``engine_version``, and assert the recomputed tier and SHA-256
``reconstruction_hash`` exactly match what was stored.

This is the auditable guarantee behind the whole system: a historical result is
not merely *plausible*, it is *reproducible* from named evidence + a named engine
version.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from engine.scoring import EvidenceEvent, classify

from storage import classifications as cls
from storage import evidence as ev


@dataclass(frozen=True)
class VerifyResult:
    ok: bool
    classification_id: str
    stored_tier: str
    recomputed_tier: str
    stored_hash: str
    recomputed_hash: str
    mismatches: List[str]
    # Bundle-provenance check (gap §3). ``None`` means no bundle was verified;
    # otherwise ``provenance_ok`` is the result of comparing the persisted
    # EvidenceBundle metadata + events against what the receipt recorded.
    bundle_id: Optional[str] = None
    provenance_ok: Optional[bool] = None
    provenance_mismatches: List[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return self.ok


def verify_events(stored_tier: str, stored_hash: str, engine_version: str,
                  events: List[EvidenceEvent]) -> VerifyResult:
    """Core comparison given the stored fields and reconstructed events."""
    recomputed = classify(events, engine_version=engine_version)
    mismatches: List[str] = []
    if recomputed.tier != stored_tier:
        mismatches.append(
            f"tier: stored={stored_tier!r} recomputed={recomputed.tier!r}"
        )
    if recomputed.reconstruction_hash != stored_hash:
        mismatches.append(
            f"reconstruction_hash: stored={stored_hash} "
            f"recomputed={recomputed.reconstruction_hash}"
        )
    return VerifyResult(
        ok=not mismatches,
        classification_id="",
        stored_tier=stored_tier,
        recomputed_tier=recomputed.tier,
        stored_hash=stored_hash,
        recomputed_hash=recomputed.reconstruction_hash,
        mismatches=mismatches,
    )


def verify_bundle_provenance(cur, bundle_id: str, *, stored_hash: str,
                             engine_version: str) -> List[str]:
    """Check a persisted ``EvidenceBundle`` is internally consistent + intact.

    Returns a list of provenance mismatches (empty == ok):

      * the bundle's persisted ``bundle_hash`` matches the engine hash recomputed
        from the events stored under that bundle (the events were not silently
        edited after the provenance row was written), and
      * that recomputed hash equals the receipt's ``reconstruction_hash`` (the
        bundle is the one that produced this classification), and
      * the bundle round-trips: reconstructing it from storage and re-serializing
        reproduces the persisted provenance metadata byte-for-byte.
    """
    mismatches: List[str] = []
    row = ev.get_bundle_row(cur, bundle_id)
    if row is None:
        return [f"bundle {bundle_id} not found"]

    bundle = ev.get_evidence_bundle(cur, bundle_id)
    if bundle is None:
        return [f"bundle {bundle_id} could not be reconstructed"]
    recomputed = classify(bundle.events, engine_version=engine_version)

    if recomputed.reconstruction_hash != row["bundle_hash"]:
        mismatches.append(
            f"bundle_hash: stored={row['bundle_hash']} "
            f"recomputed={recomputed.reconstruction_hash}"
        )
    if recomputed.reconstruction_hash != stored_hash:
        mismatches.append(
            f"bundle vs receipt hash: receipt={stored_hash} "
            f"bundle={recomputed.reconstruction_hash}"
        )

    # Provenance metadata must survive persistence unchanged.
    reserialized = bundle.to_dict()
    for key, stored_val in (
        ("provider_versions", row["provider_versions"]),
        ("warnings", row["warnings"]),
        ("match", row["match"]),
    ):
        if reserialized[key] != stored_val:
            mismatches.append(
                f"{key}: stored={stored_val!r} reconstructed={reserialized[key]!r}"
            )
    return mismatches


def verify_classification(cur, classification_id: str, *,
                          variant_key: Optional[str] = None,
                          bundle_id: Optional[str] = None) -> VerifyResult:
    """Verify one stored receipt reconstructs from its persisted evidence.

    ``cur`` must be a tenant-scoped cursor that can read the receipt (RLS). If
    ``variant_key`` is not given it is derived from the linked clinical variant's
    public coordinates — the same key research evidence is stored under.

    When ``bundle_id`` is given the scored events are taken from that specific
    persisted :class:`~evidence.model.EvidenceBundle` (not all events under the
    variant key) and the bundle's provenance metadata is additionally verified
    (gap §3); ``bundle_id`` may also be auto-discovered by matching a stored
    bundle's hash to the receipt's ``reconstruction_hash``.
    """
    row = cls.get_classification(cur, classification_id)
    if row is None:
        raise LookupError(
            f"classification {classification_id} not visible to this session"
        )

    variant_keys: List[str] = []
    if variant_key is None:
        cur.execute(
            "SELECT build, chrom, pos, ref, alt FROM clinical.variant "
            "WHERE variant_id = %s",
            (row["variant_id"],),
        )
        v = cur.fetchone()
        if v is None:
            raise LookupError(f"variant {row['variant_id']} not found")
        variant_key = cls.variant_key(
            v["chrom"], v["pos"], v["ref"], v["alt"], build=v["build"]
        )
        variant_keys.append(variant_key)
        # Providers may key external caches as ``chrom-pos-ref-alt``. Treat that
        # as an equivalent public coordinate key for read-side discovery.
        provider_key = cls.variant_key(
            v["chrom"], v["pos"], v["ref"], v["alt"], build=""
        ).lstrip("-")
        if provider_key != variant_key:
            variant_keys.append(provider_key)
    else:
        variant_keys.append(variant_key)

    # Auto-discover the bundle for this receipt when not supplied: the one whose
    # persisted hash matches the receipt (the bundle that produced this result).
    if bundle_id is None:
        for candidate_key in variant_keys:
            for b in ev.get_bundles_for_variant(cur, candidate_key):
                if b["bundle_hash"] == row["reconstruction_hash"]:
                    bundle_id = str(b["bundle_id"])
                    variant_key = candidate_key
                    break
            if bundle_id is not None:
                break

    if bundle_id is not None:
        events = ev.get_bundle_events(cur, bundle_id)
    else:
        events = []
        for candidate_key in variant_keys:
            events = ev.get_evidence_events(cur, candidate_key)
            if events:
                variant_key = candidate_key
                break

    result = verify_events(
        stored_tier=row["tier"],
        stored_hash=row["reconstruction_hash"],
        engine_version=row["engine_version"],
        events=events,
    )

    provenance_ok: Optional[bool] = None
    provenance_mismatches: List[str] = []
    if bundle_id is not None:
        provenance_mismatches = verify_bundle_provenance(
            cur, bundle_id, stored_hash=row["reconstruction_hash"],
            engine_version=row["engine_version"],
        )
        provenance_ok = not provenance_mismatches

    # Re-stamp with the real id (dataclass is frozen, so rebuild).
    return VerifyResult(
        ok=result.ok and (provenance_ok is not False),
        classification_id=classification_id,
        stored_tier=result.stored_tier,
        recomputed_tier=result.recomputed_tier,
        stored_hash=result.stored_hash,
        recomputed_hash=result.recomputed_hash,
        mismatches=result.mismatches,
        bundle_id=bundle_id,
        provenance_ok=provenance_ok,
        provenance_mismatches=provenance_mismatches,
    )
