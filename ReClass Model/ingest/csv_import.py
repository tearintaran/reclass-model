"""Validated CSV/TSV batch variant import (job1 task 5).

The CSV front end to the shared variant-import engine in
:mod:`ingest.vcf_import`. It maps a delimited table to the parsed-row shape that
:func:`ingest.vcf_import.build_import_report` consumes, so CSV import inherits the
exact same identity normalization, duplicate detection, evidence-resolution preview,
and dry-run reporting as VCF — no normalization logic is duplicated here.

Columns are matched by a small alias table (case-insensitive), so the common
spellings (``chrom``/``chromosome``/``chr``, ``pos``/``position``, ``ref``/``alt``)
all work. A row may instead carry a single ``variant_key`` column
(``GRCh38-1-100-A-G`` or ``1-100-A-G``); it is parsed back into a locus. A row that
carries neither a usable locus nor a parseable key yields a recorded ``error`` row
(never silently dropped).
"""

from __future__ import annotations

import csv
import io
import os
import sys
from typing import Any, Dict, List, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from engine.normalize import DEFAULT_BUILD, parse_key  # noqa: E402
from ingest.vcf_import import DEFAULT_RESOLVE_LIMIT, build_import_report  # noqa: E402

#: Case-insensitive column aliases -> canonical field.
_COLUMN_ALIASES = {
    "chrom": "chrom", "chromosome": "chrom", "chr": "chrom", "#chrom": "chrom",
    "pos": "pos", "position": "pos", "start": "pos",
    "ref": "ref", "reference": "ref", "ref_allele": "ref",
    "alt": "alt", "alternate": "alt", "alt_allele": "alt", "allele": "alt",
    "id": "id", "rsid": "id", "variation_id": "id", "name": "id",
    "variant_key": "variant_key", "key": "variant_key",
    "gene": "gene",
}


def _canonical_columns(fieldnames: Optional[List[str]]) -> Dict[str, str]:
    """Map each source column name to its canonical field (unknowns are ignored)."""
    mapping: Dict[str, str] = {}
    for name in fieldnames or []:
        canon = _COLUMN_ALIASES.get(str(name).strip().lower())
        if canon is not None:
            mapping[name] = canon
    return mapping


def _row_from_key(variant_key: str) -> Optional[Dict[str, Any]]:
    try:
        p = parse_key(variant_key)
    except ValueError:
        return None
    return {"chrom": p["chrom"], "pos": p["pos"], "ref": p["ref"], "alt": p["alt"]}


def parse_csv(text: str, *, delimiter: Optional[str] = None) -> List[Dict[str, Any]]:
    """Parse CSV/TSV text into the parsed-row shape used by the import engine.

    The delimiter is sniffed (comma or tab) when not given. Each row becomes a
    ``{chrom, pos, ref, alt, id, gene, line}`` dict, or an ``{error}`` row when it
    lacks a usable locus / key or has a non-integer position.
    """
    if delimiter is None:
        head = text.splitlines()[0] if text.strip() else ""
        delimiter = "\t" if head.count("\t") > head.count(",") else ","
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    colmap = _canonical_columns(reader.fieldnames)
    rows: List[Dict[str, Any]] = []
    # DictReader consumes the header line, so data starts at source line 2.
    for offset, raw_row in enumerate(reader, start=2):
        canon: Dict[str, Any] = {}
        for src, value in raw_row.items():
            field = colmap.get(src)
            if field is not None and value not in (None, ""):
                canon[field] = value.strip() if isinstance(value, str) else value

        if "variant_key" in canon and not all(k in canon for k in ("chrom", "pos", "ref", "alt")):
            locus = _row_from_key(str(canon["variant_key"]))
            if locus is None:
                rows.append({"line": offset, "error": "unparseable_variant_key",
                             "raw": canon.get("variant_key")})
                continue
            canon.update(locus)

        if not all(k in canon for k in ("chrom", "pos", "ref", "alt")):
            rows.append({"line": offset, "error": "missing_locus_columns", "raw": dict(raw_row)})
            continue
        try:
            pos_int = int(canon["pos"])
        except (ValueError, TypeError):
            rows.append({"line": offset, "error": "non_integer_position", "raw": canon.get("pos")})
            continue
        rows.append({
            "line": offset, "chrom": canon["chrom"], "pos": pos_int,
            "ref": canon["ref"], "alt": canon["alt"],
            "id": canon.get("id"), "gene": canon.get("gene"), "multiallelic": False,
        })
    return rows


def import_csv(
    text: str,
    *,
    delimiter: Optional[str] = None,
    build: str = DEFAULT_BUILD,
    reference: Any = None,
    resolver: Any = None,
    providers: Optional[List[str]] = None,
    resolve_limit: int = DEFAULT_RESOLVE_LIMIT,
) -> Dict[str, Any]:
    """Parse + normalize + dedup + (optionally) preview-resolve a CSV/TSV (dry-run)."""
    rows = parse_csv(text, delimiter=delimiter)
    return build_import_report(
        rows, fmt="csv", build=build, reference=reference,
        resolver=resolver, providers=providers, resolve_limit=resolve_limit,
    )
