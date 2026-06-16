"""Enrich the ClinVar benchmark with REAL gnomAD v4.1 allele frequencies.

Thin CLI over :class:`evidence.gnomad.GnomadProvider`. The targeted GraphQL lookup,
the popmax-FAF-with-AF-fallback logic, and the response cache that used to live
inline here now belong to the provider. This script just drives it over the
benchmark's missense-SNV loci, persists the provider cache, and writes an enriched
benchmark COPY.

For each variant the provider requests `joint.faf95.popmax` -- the popmax
*filtering* allele frequency gnomAD recommends for ACMG frequency criteria
(BA1/BS1/PM2) -- and falls back to the larger raw genome/exome AF when no FAF is
available. Absence from gnomAD is recorded as *unknown* evidence, never AF 0, and is
kept distinct from a transport failure.

The provider cache (`data/cache/providers/gnomad_cache.json`) makes repeated runs
offline after the first build and keeps `fetch` deterministic for a fixed snapshot.
This script does NOT rewrite the committed `clinvar_real_v1.json` fixture; enriched
output goes under `data/cache/providers/` only.

Usage (from ``ReClass Model/``):
    python3 ingest/enrich_gnomad.py [limit]      # default limit = 200
    python3 ingest/enrich_gnomad.py all          # query every missense-SNV locus
"""

from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from evidence.gnomad import (  # noqa: E402
    GnomadCache,
    GnomadProvider,
    curl_fetcher,
)

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FIXTURE = os.path.join(ROOT, "validation", "fixtures", "clinvar_real_v1.json")
CACHE = os.path.join(ROOT, "data", "cache", "providers", "gnomad_cache.json")
OUT = os.path.join(ROOT, "data", "cache", "providers", "clinvar_gnomad_enriched.json")


def _polite_curl_fetcher(variant_id: str) -> dict:
    """curl fetcher that rate-limits itself to be kind to the public endpoint."""
    result = curl_fetcher(variant_id)
    time.sleep(0.15)
    return result


def main() -> None:
    arg = sys.argv[1] if len(sys.argv) > 1 else "200"
    limit = None if arg.lower() == "all" else int(arg)

    with open(FIXTURE) as f:
        benchmark = json.load(f)

    todo = [c for c in benchmark["cases"]
            if c.get("locus", {}).get("snv") and c["locus"].get("missense")]
    if limit is not None:
        todo = todo[:limit]
    print(f"Querying gnomAD v4.1 for {len(todo)} loci "
          f"({'all' if limit is None else f'limit={limit}'}) ...")

    # Reuse any prior cache so re-runs are offline / incremental; retry past failures.
    cache = GnomadCache.from_cache(CACHE)
    provider = GnomadProvider(cache, fetcher=_polite_curl_fetcher, retry_failed=True)

    for i, case in enumerate(todo, 1):
        bundle = provider.fetch(case)
        if bundle.match and bundle.match.get("gnomad_match") and bundle.match.get("af") is not None:
            case["signals"]["gnomad_af"] = bundle.match["af"]
            case["signals"].setdefault("_af_source", "gnomAD_v4.1_faf95")
        if i % 25 == 0:
            print(f"  {i}/{len(todo)}  {provider.stats.as_dict()}")

    cache.to_cache(CACHE)

    s = provider.stats.as_dict()
    benchmark["note"] += (f" gnomAD v4.1 popmax FAF applied to {s['matched']} loci "
                          f"(targeted API lookup via GnomadProvider).")
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as f:
        json.dump(benchmark, f, indent=2)
        f.write("\n")

    print(f"Done. Provider stats: {s}")
    print(f"Cache: {len(cache)} variants -> {CACHE}")
    print(f"Wrote enriched benchmark copy -> {OUT}")


if __name__ == "__main__":
    main()
