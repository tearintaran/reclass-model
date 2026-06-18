"""Provenance manifests for provider source caches (job1 task 2).

Every provider cache (REVEL, AlphaMissense, conservation, gene constraint, and the
validated functional/phenotype sources) is a *local, regenerable* snapshot of an
external source. To make a snapshot auditable and reproducible, this module writes,
next to the cache file, a small ``<cache>.manifest.json`` recording:

  * the **source** and **source version** the cache was built from,
  * a **checksum** (SHA-256) of the cache file's exact bytes,
  * the **access date** the source was read (supplied by the caller, never the wall
    clock, so a rebuild from the same inputs is byte-identical), and
  * the cache filename, byte size, and record count.

Two invariants this enforces (gap.md data-governance / job1 task 2):

  * **Byte-stable rebuild.** :func:`write_cache` serializes the cache with sorted
    keys, fixed indentation, and a trailing newline; given the same payload it writes
    byte-identical output, so the recorded checksum is reproducible.
  * **Deterministic provenance.** The access date is an explicit argument (an ISO
    date string), so the manifest carries no hidden wall-clock state and tests run
    fully offline.

The manifest is provenance-only (no source data), and -- like the cache itself --
lives under ``data/cache/providers/`` and is local/regenerable (it is gitignored
alongside the cache; see ``data/cache/providers/README.md``).
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any, Dict, Optional

#: Suffix of the provenance sidecar written next to a cache file.
MANIFEST_SUFFIX = ".manifest.json"


def manifest_path_for(cache_path: str) -> str:
    """Path of the provenance manifest for a cache file (``<cache>.manifest.json``)."""
    return cache_path + MANIFEST_SUFFIX


def canonical_json(payload: Dict[str, Any]) -> str:
    """Canonical, byte-stable JSON for a cache payload.

    Sorted keys + 2-space indent + trailing newline. The same payload always renders
    the same bytes, so a rebuild from identical inputs produces an identical file (and
    therefore an identical checksum).
    """
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_sha256(path: str) -> str:
    """Lowercase hex SHA-256 of a file's bytes (streamed, memory-flat)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def build_manifest(
    *,
    cache_path: str,
    cache_bytes: bytes,
    provider: str,
    source: str,
    source_version: str,
    access_date: str,
    record_count: Optional[int] = None,
    source_url: Optional[str] = None,
    notes: Optional[str] = None,
) -> Dict[str, Any]:
    """Build the manifest dict for a written cache (pure -- no I/O).

    ``access_date`` is the caller-supplied ISO date the source was read; it is not
    derived from the clock so the manifest is deterministic and offline-testable.
    """
    return {
        "manifest_version": "1.0.0",
        "provider": provider,
        "cache_filename": os.path.basename(cache_path),
        "source": source,
        "source_version": source_version,
        "source_url": source_url,
        "access_date": access_date,
        "checksum_algorithm": "sha256",
        "checksum": _sha256_bytes(cache_bytes),
        "size_bytes": len(cache_bytes),
        "record_count": record_count,
        "notes": notes,
    }


def write_cache(
    payload: Dict[str, Any],
    cache_path: str,
    *,
    provider: str,
    source: str,
    source_version: str,
    access_date: str,
    record_count: Optional[int] = None,
    source_url: Optional[str] = None,
    notes: Optional[str] = None,
) -> Dict[str, Any]:
    """Write a cache file + its provenance manifest, byte-stably. Returns the manifest.

    The cache is serialized via :func:`canonical_json` so repeated builds from the
    same ``payload`` are byte-identical. The manifest is written next to it as
    ``<cache>.manifest.json`` and records the checksum of the exact cache bytes plus
    the supplied source/version/access-date provenance (job1 task 2).
    """
    os.makedirs(os.path.dirname(cache_path) or ".", exist_ok=True)
    cache_text = canonical_json(payload)
    cache_bytes = cache_text.encode("utf-8")
    with open(cache_path, "w", encoding="utf-8") as f:
        f.write(cache_text)

    manifest = build_manifest(
        cache_path=cache_path,
        cache_bytes=cache_bytes,
        provider=provider,
        source=source,
        source_version=source_version,
        access_date=access_date,
        record_count=record_count,
        source_url=source_url,
        notes=notes,
    )
    with open(manifest_path_for(cache_path), "w", encoding="utf-8") as f:
        f.write(canonical_json(manifest))
    return manifest


def read_manifest(cache_path: str) -> Optional[Dict[str, Any]]:
    """Load a cache's manifest, or ``None`` if absent/unreadable."""
    path = manifest_path_for(cache_path)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def verify_cache(cache_path: str) -> Dict[str, Any]:
    """Verify a cache file against its recorded manifest checksum.

    Returns ``{cache_exists, manifest_exists, recorded_checksum, actual_checksum,
    checksum_match}``. ``checksum_match`` is ``None`` when either the cache or the
    manifest (or its checksum) is missing -- "no expectation", never a false pass.
    """
    cache_exists = os.path.isfile(cache_path)
    manifest = read_manifest(cache_path)
    actual = file_sha256(cache_path) if cache_exists else None
    recorded = manifest.get("checksum") if manifest else None
    match: Optional[bool]
    if actual is None or recorded is None:
        match = None
    else:
        match = actual.lower() == str(recorded).lower()
    return {
        "cache_path": cache_path,
        "cache_exists": cache_exists,
        "manifest_exists": manifest is not None,
        "recorded_checksum": recorded,
        "actual_checksum": actual,
        "checksum_match": match,
    }
