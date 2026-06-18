"""Local GRCh38 reference-cache locating, validation, and status.

The engine already reads the genome through ``engine.reference.FastaReference`` for
reference-anchored left-alignment. What was missing was a *standard, testable* way
to answer operational questions about the local FASTA cache:

  * where does the engine expect the GRCh38 FASTA to live,
  * is the file (and its ``.fai`` sibling) actually present,
  * does it match an expected checksum (when one is provided), and
  * can ``FastaReference`` actually open it?

This module is a thin wrapper around ``FastaReference``; it never downloads a
genome, never mutates the FASTA, and does not change normalization behavior. Large
FASTA files are expected to be a local-only cache under ``data/reference/`` and kept
out of source control.

CLI:

    ../.venv/bin/python -m engine.reference_cache --status

The status command exits cleanly even when the FASTA is missing; it simply reports
the configured path and that the file does not exist.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from .reference import FastaReference, ReferenceLookupError

ENV_FASTA_PATH = "RECLASS_GRCH38_FASTA"
ENV_FASTA_SHA256 = "RECLASS_GRCH38_SHA256"
DEFAULT_FASTA_FILENAME = "GRCh38.fa"
DEFAULT_BUILD = "GRCh38"
#: Sidecar that records the installed FASTA's source/version/checksum (job1 task 1).
#: It is small and provenance-only (no genome bases), so it is safe to keep next to
#: the gitignored FASTA as a tamper-evidence record of exactly what was installed.
DEFAULT_META_SUFFIX = ".meta.json"
_READ_CHUNK = 1024 * 1024  # 1 MiB streaming chunks for checksum


@dataclass
class ReferenceCacheConfig:
    """Where the local reference lives and what it should be.

    ``path`` is resolved explicitly (see :func:`default_reference_path`); ``sha256``
    is the *expected* lowercase hex digest, when one is known, so status can report a
    checksum match. ``None`` means "no expectation, do not verify".
    """

    path: str
    build: str = DEFAULT_BUILD
    sha256: Optional[str] = None


def project_root(start: Optional[str] = None) -> str:
    """Absolute path to the ``ReClass Model`` directory that contains ``engine/``.

    ``start`` defaults to this file's location; we walk up to the package parent so
    the default cache path is stable regardless of the process working directory.
    """
    here = os.path.abspath(start or __file__)
    # engine/reference_cache.py -> engine/ -> ReClass Model/
    return os.path.dirname(os.path.dirname(here))


def default_reference_path(project_root_dir: Optional[str] = None) -> str:
    """Resolve the configured FASTA path.

    Precedence:
      1. the ``RECLASS_GRCH38_FASTA`` environment variable, if set and non-empty;
      2. otherwise ``<project_root>/data/reference/GRCh38.fa``.
    """
    override = os.environ.get(ENV_FASTA_PATH)
    if override:
        return os.path.abspath(os.path.expanduser(override))
    root = project_root_dir or project_root()
    return os.path.join(root, "data", "reference", DEFAULT_FASTA_FILENAME)


def default_config(project_root_dir: Optional[str] = None) -> ReferenceCacheConfig:
    """Build a :class:`ReferenceCacheConfig` from the environment / default path.

    The optional expected checksum is read from ``RECLASS_GRCH38_SHA256`` so a site
    can pin the exact genome build it intends to use; when unset there is no
    expectation and status simply reports the file's actual digest.
    """
    expected = os.environ.get(ENV_FASTA_SHA256) or None
    return ReferenceCacheConfig(
        path=default_reference_path(project_root_dir),
        build=DEFAULT_BUILD,
        sha256=expected,
    )


def load_default_reference(
    config: Optional[ReferenceCacheConfig] = None,
    *,
    allow_missing: bool = True,
) -> Optional[FastaReference]:
    """Discover and open the local GRCh38 FASTA for reference-backed normalization.

    Resolution order is ``RECLASS_GRCH38_FASTA`` then the default cache path (see
    :func:`default_reference_path`). Returns a ready :class:`FastaReference` when the
    file is present (verifying ``RECLASS_GRCH38_SHA256`` if set, so a checksum
    mismatch raises rather than silently using the wrong genome). When the file is
    absent it returns ``None`` (``allow_missing=True``) so normalization workflows can
    fall back to reference-free behavior and flag indels, or raises
    :class:`ReferenceLookupError` when a reference is required.
    """
    config = config or default_config()
    if not os.path.isfile(config.path):
        if allow_missing:
            return None
        raise ReferenceLookupError(
            f"GRCh38 reference FASTA not found at {config.path}. Set "
            f"${ENV_FASTA_PATH} or place the file at the default cache path."
        )
    return load_reference(config)


def file_sha256(path: str) -> str:
    """Stream the file and return its lowercase hex SHA-256 digest.

    Streaming keeps memory flat for whole-genome FASTAs (multi-GB), so this is safe
    to call against the real cache.
    """
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(_READ_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def meta_path_for(fasta_path: str) -> str:
    """Path of the provenance sidecar for a FASTA (``<fasta>.meta.json``)."""
    return fasta_path + DEFAULT_META_SUFFIX


def read_metadata(meta_path: str) -> Optional[dict]:
    """Load a recorded provenance sidecar, or None if absent/unreadable."""
    if not os.path.isfile(meta_path):
        return None
    try:
        with open(meta_path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def record_metadata(
    config: ReferenceCacheConfig,
    *,
    source: Optional[str] = None,
    source_url: Optional[str] = None,
    version: Optional[str] = None,
    notes: Optional[str] = None,
    meta_path: Optional[str] = None,
) -> dict:
    """Record the installed FASTA's source/version/checksum to a sidecar (task 1).

    Computes the SHA-256 of the file actually on disk (so the digest is the genome
    you installed, not a guess), captures its size and an access timestamp, and
    writes ``<fasta>.meta.json``. This is the "record the FASTA source, version, and
    a checksum" step -- the whole-genome file stays a local, gitignored cache while
    this small provenance record can be kept (and pinned via ``RECLASS_GRCH38_SHA256``).
    Raises :class:`ReferenceLookupError` if the FASTA is not present to checksum.
    """
    if not os.path.isfile(config.path):
        raise ReferenceLookupError(
            f"cannot record metadata: FASTA not found at {config.path}. Install it first."
        )
    digest = file_sha256(config.path)
    meta = {
        "build": config.build,
        "fasta_path": config.path,
        "fasta_filename": os.path.basename(config.path),
        "source": source,
        "source_url": source_url,
        "version": version,
        "sha256": digest,
        "size_bytes": os.path.getsize(config.path),
        "recorded_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "notes": notes,
    }
    target = meta_path or meta_path_for(config.path)
    with open(target, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, sort_keys=True)
        f.write("\n")
    return meta


def reference_status(config: ReferenceCacheConfig) -> dict:
    """Build a JSON-serializable status report for a configured reference cache.

    Never raises for an absent/broken FASTA: load failures are captured in the
    ``error`` field so the caller (and the CLI) can report cleanly.
    """
    path = config.path
    fai_path = path + ".fai"
    meta_path = meta_path_for(path)
    exists = os.path.isfile(path)
    metadata = read_metadata(meta_path)

    status: dict = {
        "path": path,
        "build": config.build,
        "exists": exists,
        "fai_exists": os.path.isfile(fai_path),
        "fai_path": fai_path,
        "expected_sha256": config.sha256,
        "actual_sha256": None,
        "checksum_match": None,
        "meta_path": meta_path,
        "metadata": metadata,
        "metadata_sha256_match": None,
        "loadable": False,
        "contigs": None,
        "error": None,
    }

    if not exists:
        status["error"] = f"reference FASTA not found at {path}"
        return status

    try:
        status["actual_sha256"] = file_sha256(path)
    except OSError as exc:  # pragma: no cover - unusual IO failure
        status["error"] = f"could not read FASTA for checksum: {exc}"
        return status

    if config.sha256 is not None:
        status["checksum_match"] = (
            status["actual_sha256"].lower() == config.sha256.lower()
        )

    # Independent cross-check against the recorded provenance sidecar: if a
    # metadata file pins a checksum, confirm the file on disk still matches it.
    if metadata and metadata.get("sha256"):
        status["metadata_sha256_match"] = (
            status["actual_sha256"].lower() == str(metadata["sha256"]).lower()
        )

    try:
        ref = FastaReference(path)
        status["loadable"] = True
        status["contigs"] = len(getattr(ref, "_index", {}) or {})
    except Exception as exc:  # noqa: BLE001 - report any load failure as status
        status["error"] = f"FastaReference could not load file: {exc}"

    return status


def load_reference(config: ReferenceCacheConfig) -> FastaReference:
    """Open the configured FASTA as a ``FastaReference``.

    When an expected ``sha256`` is set it is verified first; a mismatch raises
    ``ValueError`` so callers never silently use the wrong genome.
    """
    if config.sha256 is not None:
        actual = file_sha256(config.path)
        if actual.lower() != config.sha256.lower():
            raise ValueError(
                f"checksum mismatch for {config.path}: expected {config.sha256}, "
                f"got {actual}"
            )
    return FastaReference(config.path)


def _config_from_args(args: argparse.Namespace) -> ReferenceCacheConfig:
    path = args.path or default_reference_path()
    sha256 = args.sha256 or os.environ.get(ENV_FASTA_SHA256) or None
    return ReferenceCacheConfig(path=path, build=args.build, sha256=sha256)


def _format_status(status: dict) -> str:
    def mark(value: Optional[bool]) -> str:
        if value is None:
            return "n/a"
        return "yes" if value else "no"

    lines = [
        "GRCh38 reference cache status",
        f"  configured path : {status['path']}",
        f"  build           : {status['build']}",
        f"  file exists     : {mark(status['exists'])}",
        f"  .fai exists     : {mark(status['fai_exists'])}",
        f"  expected sha256 : {status['expected_sha256'] or 'n/a'}",
        f"  actual sha256   : {status['actual_sha256'] or 'n/a'}",
        f"  checksum match  : {mark(status['checksum_match'])}",
        f"  loadable        : {mark(status['loadable'])}",
        f"  contigs         : {status['contigs'] if status['contigs'] is not None else 'n/a'}",
    ]
    meta = status.get("metadata")
    if meta:
        lines.append(f"  recorded source : {meta.get('source') or 'n/a'}")
        lines.append(f"  recorded version: {meta.get('version') or 'n/a'}")
        lines.append(f"  recorded sha256 : {meta.get('sha256') or 'n/a'}")
        lines.append(f"  meta matches    : {mark(status.get('metadata_sha256_match'))}")
    else:
        lines.append(f"  provenance meta : none ({status.get('meta_path')})")
    if status["error"]:
        lines.append(f"  note            : {status['error']}")
    return "\n".join(lines)


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="engine.reference_cache",
        description="Report the status of the local GRCh38 reference FASTA cache.",
    )
    parser.add_argument(
        "--status", action="store_true",
        help="report the status of the configured reference cache",
    )
    parser.add_argument(
        "--record", action="store_true",
        help="record the installed FASTA's checksum + provenance to its .meta.json sidecar",
    )
    parser.add_argument(
        "--path", default=None,
        help=f"FASTA path (default: ${ENV_FASTA_PATH} or data/reference/"
             f"{DEFAULT_FASTA_FILENAME})",
    )
    parser.add_argument(
        "--build", default=DEFAULT_BUILD, help="genome build label (default: GRCh38)",
    )
    parser.add_argument(
        "--sha256", default=None,
        help="expected lowercase hex SHA-256 to verify against",
    )
    parser.add_argument("--source", default=None,
                        help="(with --record) human-readable FASTA source/distributor")
    parser.add_argument("--source-url", default=None,
                        help="(with --record) download URL the FASTA came from")
    parser.add_argument("--source-version", default=None,
                        help="(with --record) source release/version label")
    parser.add_argument("--note", default=None,
                        help="(with --record) free-text note for the provenance record")
    parser.add_argument(
        "--json", action="store_true", help="emit the report as JSON",
    )
    args = parser.parse_args(argv)
    config = _config_from_args(args)

    if args.record:
        try:
            meta = record_metadata(
                config, source=args.source, source_url=args.source_url,
                version=args.source_version, notes=args.note,
            )
        except ReferenceLookupError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        if args.json:
            print(json.dumps(meta, indent=2, sort_keys=True))
        else:
            print(f"Recorded provenance -> {meta_path_for(config.path)}")
            print(f"  source  : {meta.get('source') or 'n/a'}")
            print(f"  version : {meta.get('version') or 'n/a'}")
            print(f"  sha256  : {meta['sha256']}")
        return 0

    # --status is the default action so bare invocation is useful.
    status = reference_status(config)
    if args.json:
        print(json.dumps(status, indent=2, sort_keys=True))
    else:
        print(_format_status(status))
    return 0


if __name__ == "__main__":
    sys.exit(main())
