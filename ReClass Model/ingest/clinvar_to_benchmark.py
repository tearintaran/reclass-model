"""Build a REAL benchmark from the ClinVar VCF (highest-confidence subset).

Unlike the ClinGen benchmark (which carries the panel's applied ACMG *criteria*),
ClinVar gives us the expert label but NOT structured per-criterion evidence. The
only machine-readable evidence we can attach from ClinVar is population frequency
(AF_*) and -- via enrichment -- a REVEL computational score. So this benchmark
deliberately exposes the *evidence-integration gap*:

    Frequency + a computational predictor alone CANNOT reproduce expert
    pathogenic calls (those rest on PVS1/PS3/PM3/... evidence ClinVar doesn't
    encode here). Expect strong concordance on benign/frequency-driven calls and
    weak recall on pathogenic ones -- which is the honest, instructive result and
    motivates the provider-backed evidence-integration layer and remaining evidence
    coverage work described in the README.

Filters to CLNREVSTAT in {reviewed_by_expert_panel, practice_guideline} (ClinVar's
top review tiers) and the five standard ACMG tiers.

Input : data/raw/clinvar_GRCh38.vcf.gz
Output: validation/fixtures/clinvar_real_v1.json   (engine fixture schema)
        Each case also carries a `locus` (chr/pos/ref/alt) so REVEL enrichment can
        fill in signals.revel afterwards.

Run:  python3 ingest/clinvar_to_benchmark.py
"""

from __future__ import annotations

import gzip
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
RAW = os.path.join(ROOT, "data", "raw", "clinvar_GRCh38.vcf.gz")
OUT = os.path.join(ROOT, "validation", "fixtures", "clinvar_real_v1.json")

KEEP_REVSTAT = {"reviewed_by_expert_panel", "practice_guideline"}

CLNSIG_TO_TIER = {
    "Pathogenic": "Pathogenic",
    "Likely_pathogenic": "Likely Pathogenic",
    "Uncertain_significance": "VUS",
    "Likely_benign": "Likely Benign",
    "Benign": "Benign",
}


def parse_info(info: str) -> dict:
    out = {}
    for field in info.split(";"):
        if "=" in field:
            k, _, v = field.partition("=")
            out[k] = v
    return out


def popmax_af(info: dict) -> float | None:
    """Use the max of the bundled ClinVar frequency sources as a popmax proxy.

    NOTE: AF_EXAC/AF_ESP/AF_TGP are legacy sources, not gnomAD v4.1 popmax. They
    are a stand-in so the frequency rules (BA1/BS1/PM2) fire; see ingest/README for
    the gnomAD v4.1 upgrade path.
    """
    vals = []
    for key in ("AF_EXAC", "AF_ESP", "AF_TGP"):
        if key in info:
            try:
                vals.append(float(info[key]))
            except ValueError:
                pass
    return max(vals) if vals else None


def main() -> None:
    if not os.path.exists(RAW):
        raise SystemExit(f"Missing {RAW}. Download the ClinVar GRCh38 VCF first.")

    cases = []
    counts = {"kept": 0, "skip_revstat": 0, "skip_sig": 0}

    with gzip.open(RAW, "rt", encoding="utf-8") as f:
        for line in f:
            if line.startswith("#"):
                continue
            chrom, pos, vid, ref, alt, _q, _flt, info_str = line.rstrip("\n").split("\t")[:8]
            info = parse_info(info_str)

            revstat = info.get("CLNREVSTAT", "")
            if revstat not in KEEP_REVSTAT:
                counts["skip_revstat"] += 1
                continue

            tier = CLNSIG_TO_TIER.get(info.get("CLNSIG", ""))
            if tier is None:
                counts["skip_sig"] += 1
                continue

            gene = (info.get("GENEINFO", "") or "NA").split(":")[0] or "NA"
            af = popmax_af(info)
            is_snv = len(ref) == 1 and len(alt) == 1 and ref != "." and alt != "."
            is_missense = "missense_variant" in info.get("MC", "")

            signals: dict = {"criteria": []}
            if af is not None:
                signals["gnomad_af"] = af
            # signals.revel is filled later by ingest/enrich_revel.py (missense SNVs only)

            cases.append({
                "id": f"CV-{vid}",
                "gene": gene,
                "ancestry": "Unspecified",   # ClinVar carries no ancestry
                "expected": tier,
                "signals": signals,
                "locus": {"chrom": chrom, "pos": int(pos), "ref": ref, "alt": alt,
                          "snv": is_snv, "missense": is_missense},
                "provenance": {"source": "ClinVar", "clnrevstat": revstat,
                               "variation_id": vid},
            })
            counts["kept"] += 1

    benchmark = {
        "benchmark": "clinvar_real_v1",
        "engine_version": "1.0.0",
        "note": ("REAL ClinVar benchmark, top review tiers (expert panel + practice "
                 "guideline). Signals are frequency (legacy AF_*) plus REVEL where "
                 "enriched -- NOT the full ACMG evidence set. This benchmark exposes "
                 "the evidence-integration gap: pathogenic recall is expected to be "
                 "low because PVS1/PS3/PM3-type evidence is not encoded here."),
        "source_file": "data/raw/clinvar_GRCh38.vcf.gz",
        "cases": cases,
    }

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as f:
        json.dump(benchmark, f, indent=2)
        f.write("\n")

    n_af = sum(1 for c in cases if "gnomad_af" in c["signals"])
    n_mis = sum(1 for c in cases if c["locus"]["missense"] and c["locus"]["snv"])
    print(f"Wrote {len(cases)} real ClinVar cases -> {OUT}")
    print(f"  with frequency signal:    {n_af}")
    print(f"  missense SNVs (REVEL-able): {n_mis}")
    print(f"  skipped (review status):  {counts['skip_revstat']}")
    print(f"  skipped (non-standard sig): {counts['skip_sig']}")


if __name__ == "__main__":
    main()
