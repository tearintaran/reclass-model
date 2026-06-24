"""Validated VCF batch variant import: normalize, dedup, preview, dry-run (job1 task 5).

A batch of variants from a VCF must be brought to the engine's canonical identity
before it can be classified: multiallelic sites split, alleles parsimoniously
trimmed, indels reference-anchored left-aligned, and the GRCh38 build pinned. This
importer does exactly that by reusing the engine's identity layer
(``engine.normalize``) — it adds no new normalization rules — and then reports:

  * **identity normalization** — the canonical ``GRCh38-chrom-pos-ref-alt`` key for
    every parsed allele, with the normalization method and any advisory warnings
    (e.g. an indel that could not be left-aligned without a reference);
  * **duplicate detection** — variants that collapse to the same canonical key;
  * **evidence-resolution preview** — when a resolver is supplied, what evidence each
    unique variant would resolve to (counts, criteria, provider versions, warnings),
    so an operator sees coverage before committing;
  * **a dry-run report** — the whole thing computes and reports without persisting
    anything. Importing is always inspect-first.

``build_import_report`` is the shared engine for both VCF and CSV import
(``ingest.csv_import`` reuses it); :func:`parse_vcf` is the VCF-specific front end.
"""

from __future__ import annotations

import os
import sys
from typing import Any, Dict, Iterable, List, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from engine.normalize import DEFAULT_BUILD, normalize_locus  # noqa: E402

#: Cap on how many unique variants get an evidence-resolution preview in one report,
#: so a huge VCF cannot fan out unboundedly. The report flags when it was hit.
DEFAULT_RESOLVE_LIMIT = 200


def parse_vcf(text: str) -> List[Dict[str, Any]]:
    """Parse VCF text into one row per (site, ALT allele).

    Header (``#``) lines are skipped. Each data line must have at least the 5 fixed
    columns ``CHROM POS ID REF ALT``; a comma-separated ALT is split into one row per
    allele (each tagged ``multiallelic`` so the report can note the split). A line that
    cannot be parsed yields an ``{"error": ...}`` row (recorded, never silently
    dropped). ``line`` is the 1-based source line number.
    """
    rows: List[Dict[str, Any]] = []
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split("\t")
        if len(fields) < 5:
            # Tolerate whitespace-delimited VCF-like input.
            fields = line.split()
        if len(fields) < 5:
            rows.append({"line": lineno, "error": "malformed_vcf_line", "raw": raw})
            continue
        chrom, pos, vid, ref, alt = fields[0], fields[1], fields[2], fields[3], fields[4]
        try:
            pos_int = int(pos)
        except ValueError:
            rows.append({"line": lineno, "error": "non_integer_position", "raw": raw})
            continue
        alts = [a for a in str(alt).split(",") if a]
        if not alts:
            rows.append({"line": lineno, "error": "missing_alt", "raw": raw})
            continue
        multi = len(alts) > 1
        for allele in alts:
            rows.append({
                "line": lineno, "chrom": chrom, "pos": pos_int, "ref": ref,
                "alt": allele, "id": None if vid in (".", "") else vid,
                "multiallelic": multi,
            })
    return rows


def _provider_input(chrom: str, pos: int, ref: str, alt: str, build: str) -> Dict[str, Any]:
    locus = {"chrom": str(chrom), "pos": int(pos), "ref": ref, "alt": alt, "build": build}
    out: Dict[str, Any] = {"locus": locus}
    out.update(locus)
    return out


def _resolution_preview(
    resolver: Any, chrom: str, pos: int, ref: str, alt: str, build: str,
    variant_key: str, providers: Optional[List[str]],
) -> Dict[str, Any]:
    result = resolver.resolve(
        _provider_input(chrom, pos, ref, alt, build),
        variant_key=variant_key, providers=providers,
    )
    bundle = result["bundle"]
    criteria = sorted({e.acmg_criterion for e in bundle.events})
    return {
        "events": len(bundle.events),
        "criteria": criteria,
        "provider_versions": dict(bundle.provider_versions),
        "warnings": list(bundle.warnings),
        "resolved": bool(bundle.events),
    }


def build_import_report(
    rows: Iterable[Dict[str, Any]],
    *,
    fmt: str = "vcf",
    build: str = DEFAULT_BUILD,
    reference: Any = None,
    resolver: Any = None,
    providers: Optional[List[str]] = None,
    resolve_limit: int = DEFAULT_RESOLVE_LIMIT,
) -> Dict[str, Any]:
    """Normalize + dedup + (optionally) preview-resolve a set of parsed variant rows.

    Shared by VCF and CSV import. Never persists — the report is a pure, deterministic
    dry-run. ``resolver`` (an ``api.evidence_resolver.EvidenceResolver``) enables the
    per-variant evidence-resolution preview; without it the report is identity-only.
    """
    rows = list(rows)
    invalid: List[Dict[str, Any]] = []
    variants: List[Dict[str, Any]] = []
    seen: Dict[str, Dict[str, Any]] = {}     # canonical key -> first variant entry
    duplicates: Dict[str, List[Any]] = {}    # canonical key -> later occurrences' lines

    for row in rows:
        if row.get("error"):
            invalid.append({"line": row.get("line"), "error": row["error"],
                            "raw": row.get("raw")})
            continue
        result = normalize_locus(row["chrom"], row["pos"], row["ref"], row["alt"],
                                 reference=reference, build=build)
        if not result.ok or result.blocking:
            invalid.append({
                "line": row.get("line"),
                "error": "normalization_failed",
                "input": {k: row[k] for k in ("chrom", "pos", "ref", "alt")},
                "warnings": list(result.warnings),
            })
            continue
        key = result.key
        entry = {
            "key": key,
            "provider_key": result.provider_key,
            "input": {k: row[k] for k in ("chrom", "pos", "ref", "alt")},
            "id": row.get("id"),
            "method": result.method,
            "multiallelic": bool(row.get("multiallelic")),
            "warnings": list(result.warnings),
            "line": row.get("line"),
        }
        if key in seen:
            duplicates.setdefault(key, []).append(row.get("line"))
            continue
        seen[key] = entry
        variants.append(entry)

    # Evidence-resolution preview for the unique variants (bounded).
    resolution_meta: Optional[Dict[str, Any]] = None
    if resolver is not None:
        previewed = 0
        for entry in variants:
            if previewed >= resolve_limit:
                break
            inp = entry["input"]
            entry["resolution"] = _resolution_preview(
                resolver, inp["chrom"], inp["pos"], inp["ref"], inp["alt"],
                build, entry["key"], providers,
            )
            previewed += 1
        resolution_meta = {
            "previewed": previewed,
            "limit": resolve_limit,
            "capped": len(variants) > resolve_limit,
        }

    duplicate_rows = sum(len(v) for v in duplicates.values())
    return {
        "format": fmt,
        "build": build,
        "dry_run": True,
        "totals": {
            "rows": len(rows),
            "parsed": len(rows) - len(invalid),
            "invalid": len(invalid),
            "unique_variants": len(variants),
            "duplicate_rows": duplicate_rows,
        },
        "variants": variants,
        "duplicates": [
            {"key": key, "count": len(lines) + 1, "duplicate_lines": lines}
            for key, lines in sorted(duplicates.items())
        ],
        "invalid": invalid,
        "resolution": resolution_meta,
    }


def import_vcf(
    text: str,
    *,
    build: str = DEFAULT_BUILD,
    reference: Any = None,
    resolver: Any = None,
    providers: Optional[List[str]] = None,
    resolve_limit: int = DEFAULT_RESOLVE_LIMIT,
) -> Dict[str, Any]:
    """Parse + normalize + dedup + (optionally) preview-resolve a VCF text (dry-run)."""
    rows = parse_vcf(text)
    return build_import_report(
        rows, fmt="vcf", build=build, reference=reference,
        resolver=resolver, providers=providers, resolve_limit=resolve_limit,
    )
