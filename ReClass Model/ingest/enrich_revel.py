"""Enrich the ClinVar benchmark with REVEL computational scores (PP3/BP4 evidence).

Thin CLI over :class:`evidence.revel.RevelProvider`. The score-resolution and
cache-building logic that used to live inline here now belongs to the provider, so
the same REVEL evidence can be resolved repeatedly, offline once cached, and with
provenance. This script just orchestrates the provider:

  1. read the benchmark's missense-SNV loci (read-only),
  2. build (or extend) the local REVEL lookup cache by streaming `revel_all.zip`
     for exactly those loci (`data/cache/providers/revel_cache.json`),
  3. resolve each locus through the provider and write an enriched benchmark COPY.

It deliberately does NOT rewrite `validation/fixtures/clinvar_real_v1.json` in
place (that committed fixture is shared with other jobs). Enriched output and the
provider cache go under `data/cache/providers/` only.

Key = (chrom, grch38_pos, ref, alt). REVEL uses bare chromosome names (e.g. '1'),
matching the ClinVar VCF CHROM column.

Run (from ``ReClass Model/``):
    python3 ingest/enrich_revel.py
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from evidence.revel import RevelIndex, RevelProvider, variant_key  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ZIP = os.path.join(ROOT, "data", "raw", "revel_all.zip")
FIXTURE = os.path.join(ROOT, "validation", "fixtures", "clinvar_real_v1.json")
CACHE = os.path.join(ROOT, "data", "cache", "providers", "revel_cache.json")
OUT = os.path.join(ROOT, "data", "cache", "providers", "clinvar_revel_enriched.json")


def _missense_snv_cases(benchmark: dict) -> list:
    out = []
    for case in benchmark["cases"]:
        loc = case.get("locus", {})
        if loc.get("snv") and loc.get("missense"):
            out.append(case)
    return out


def _progress(scanned: int, kept: int) -> None:
    print(f"  ...scanned {scanned:,} REVEL rows, cached {kept}")


def main() -> None:
    if not os.path.exists(ZIP):
        raise SystemExit(f"Missing {ZIP}. Download REVEL first.")
    if not os.path.exists(FIXTURE):
        raise SystemExit(f"Missing {FIXTURE}. Run clinvar_to_benchmark.py first.")

    with open(FIXTURE) as f:
        benchmark = json.load(f)

    targets = _missense_snv_cases(benchmark)
    target_keys = {
        variant_key(c["locus"]["chrom"], c["locus"]["pos"], c["locus"]["ref"], c["locus"]["alt"])
        for c in targets
    }
    print(f"Target missense-SNV loci to score: {len(target_keys)}")

    # Build the provider's local lookup cache by streaming only the loci we need.
    print(f"Streaming REVEL from {ZIP} ...")
    index = RevelIndex.build_from_zip(ZIP, target_keys, progress=_progress)
    index.to_cache(CACHE)
    print(f"Built REVEL cache: {len(index)} loci -> {CACHE}")

    # Resolve every target locus through the provider (offline, from the cache).
    provider = RevelProvider(index)
    for case in targets:
        bundle = provider.fetch(case)
        if bundle.match and bundle.match.get("revel_match"):
            case["signals"]["revel"] = bundle.match["revel_score"]

    enriched = sum(1 for c in benchmark["cases"] if c["signals"].get("revel") is not None)
    benchmark["note"] += " REVEL scores enriched for missense SNVs where available (via RevelProvider)."
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as f:
        json.dump(benchmark, f, indent=2)
        f.write("\n")

    s = provider.stats.as_dict()
    print(f"Provider stats: {s}")
    print(f"{enriched} cases now carry a REVEL score.")
    print(f"Wrote enriched benchmark copy -> {OUT}")


if __name__ == "__main__":
    main()
