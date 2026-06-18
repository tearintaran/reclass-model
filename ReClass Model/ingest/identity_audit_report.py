"""Identity / normalization audit report (job1 task 2).

Re-runs the SNV/indel duplicate-and-mismatch audit (``engine.normalize.audit_loci``)
over the real benchmark loci and writes a short report to
``validation/reports/identity_audit_grch38.md`` (+ ``.json``). It records the
reference-free baseline now and the reference-backed (left-aligned) numbers when a
local GRCh38 FASTA is available -- so installing the production FASTA (task 1) and
re-running this script fills in the "after" columns with no code change.

Run from ``ReClass Model/``:

    ../.venv/bin/python ingest/identity_audit_report.py
"""

from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from engine.normalize import audit_loci, locus_from_case, normalize_locus  # noqa: E402
from engine.reference import InMemoryReference  # noqa: E402
from engine.reference_cache import default_config, load_default_reference, reference_status  # noqa: E402
from ingest.hgvs import locus_from_genomic_hgvs, parse_spdi  # noqa: E402

FIXTURES = os.path.join(ROOT, "validation", "fixtures")
REPORTS = os.path.join(ROOT, "validation", "reports")
OUT_MD = os.path.join(REPORTS, "identity_audit_grch38.md")
OUT_JSON = os.path.join(REPORTS, "identity_audit_grch38.json")


def _load_loci(fixture: str):
    path = os.path.join(FIXTURES, fixture + ".json")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        cases = json.load(f).get("cases", [])
    return [loc for loc in (locus_from_case(c) for c in cases) if loc is not None]


def _demo_reference_backed() -> dict:
    """Controlled demonstration that reference-backed left-alignment collapses
    repeat-shifted indel spellings (proves the 'after' capability offline)."""
    ref = InMemoryReference({"1": "GAAAAT"})  # G, A-run 2..5, T
    # The SAME insertion written at four positions in the A-run, plus a duplicate SNV.
    loci = [("1", 2, "A", "AA"), ("1", 3, "A", "AA"), ("1", 4, "A", "AA"),
            ("1", 5, "A", "AA"), ("1", 100, "C", "T"), ("1", 100, "C", "T")]
    free = audit_loci(loci)
    backed = audit_loci(loci, reference=ref)
    return {
        "input_loci": len(loci),
        "reference_free_indel_duplicated_loci": free["reference_free"]["indel"]["duplicated_loci"],
        "reference_backed_indel_duplicated_loci": backed["reference_backed"]["indel"]["duplicated_loci"],
        "indel_duplicates_revealed": backed["reference_backed"]["indel_duplicates_revealed"],
    }


def _demo_identity_routes() -> dict:
    """Controlled demonstration (job1 task 3) that the new ClinVar-side identity tokens
    -- NCBI SPDI and genomic HGVS -- resolve to the SAME canonical key as the native
    coordinate locus, so a case carrying any one of them joins the same record."""
    ref = InMemoryReference({"1": "GAAAAT"})  # NC_000001.11 == contig "1"
    coordinate = normalize_locus("1", 2, "A", "T", reference=ref).key  # SNV at pos 2
    spdi_loc = parse_spdi("NC_000001.11:1:A:T", ref)        # 0-based 1 -> 1-based 2
    hgvs_loc = locus_from_genomic_hgvs("NC_000001.11:g.2A>T", ref)
    spdi_key = normalize_locus(*spdi_loc, reference=ref).key if spdi_loc else None
    hgvs_key = normalize_locus(*hgvs_loc, reference=ref).key if hgvs_loc else None
    # Indel SPDI (pure deletion needs the reference for the VCF anchor).
    indel_spdi = parse_spdi("NC_000001.11:1:A:", ref)       # delete the A at pos 2
    indel_key = normalize_locus(*indel_spdi, reference=ref).key if indel_spdi else None
    return {
        "coordinate_key": coordinate,
        "spdi_key": spdi_key,
        "hgvs_g_key": hgvs_key,
        "spdi_matches_coordinate": spdi_key == coordinate,
        "hgvs_matches_coordinate": hgvs_key == coordinate,
        "indel_spdi_key": indel_key,
    }


def build_report() -> dict:
    reference = load_default_reference()  # None unless a local FASTA is installed
    status = reference_status(default_config())

    clinvar = _load_loci("clinvar_real_v1")
    clingen = _load_loci("clingen_real_v1")
    combined = clinvar + clingen  # the ClinVar<->ClinGen canonical-key JOIN surface

    audits = {
        "clinvar_real_v1": audit_loci(clinvar, reference=reference),
        "clingen_real_v1": audit_loci(clingen, reference=reference),
        "combined_join_surface": audit_loci(combined, reference=reference),
    }
    return {
        "reference_available": reference is not None,
        "reference_status": {k: status.get(k) for k in
                             ("path", "exists", "loadable", "metadata_sha256_match")},
        "audits": audits,
        "reference_backed_demonstration": _demo_reference_backed(),
        "identity_route_demonstration": _demo_identity_routes(),
    }


def _dup(stats: dict) -> str:
    return (f"{stats['loci']} loci, {stats['distinct_keys']} distinct, "
            f"{stats['collision_keys']} collision keys, "
            f"{stats['duplicated_loci']} duplicated loci")


def _audit_md(name: str, a: dict) -> list:
    lines = [f"### `{name}`", ""]
    lines.append(f"- Total loci: {a['total_loci']} (SNV {a['snv']}, indel {a['indel']}; "
                 f"invalid {a['invalid_loci']})")
    lines.append(f"- Reference-free SNV:   {_dup(a['reference_free']['snv'])}")
    lines.append(f"- Reference-free indel: {_dup(a['reference_free']['indel'])}")
    if a["reference_available"] and a.get("reference_backed"):
        rb = a["reference_backed"]
        lines.append(f"- Reference-backed SNV:   {_dup(rb['snv'])}")
        lines.append(f"- Reference-backed indel: {_dup(rb['indel'])} "
                     f"(duplicates revealed: {rb['indel_duplicates_revealed']})")
        lines.append(f"- Reference mismatches: {rb['reference_mismatch']}; "
                     f"lookup failures: {rb['reference_lookup_failed']}")
    else:
        lines.append(f"- Reference-backed: **pending FASTA install** "
                     f"(indels not left-aligned: {a.get('indel_not_left_aligned')})")
    lines.append("")
    return lines


def render_markdown(report: dict) -> str:
    lines = ["# Identity / normalization audit — GRCh38 (job1 task 2)", ""]
    avail = report["reference_available"]
    lines.append(f"**Reference-backed normalization available:** "
                 f"{'yes' if avail else 'no (reference-free baseline only)'}  ")
    lines.append(f"**Configured FASTA:** `{report['reference_status']['path']}` "
                 f"(exists: {report['reference_status']['exists']})")
    lines.append("")
    lines.append("SNV and indel duplicate/mismatch rates over the real benchmark loci, "
                 "before vs after reference-anchored left-alignment. SNV keys are "
                 "identical in both views (no reference needed); indels collapse "
                 "repeat-shifted spellings only after reference-backed left-alignment.")
    lines.append("")
    lines.append("## Audits")
    lines.append("")
    for name, a in report["audits"].items():
        lines.extend(_audit_md(name, a))
    d = report["reference_backed_demonstration"]
    lines.append("## Reference-backed capability (controlled demonstration)")
    lines.append("")
    lines.append("A controlled example over an in-memory reference (`1` = `GAAAAT`) with "
                 "four repeat-shifted spellings of one insertion confirms the machinery "
                 "collapses them once a reference is supplied:")
    lines.append("")
    lines.append(f"- input loci: {d['input_loci']}")
    lines.append(f"- reference-free indel duplicated loci: {d['reference_free_indel_duplicated_loci']}")
    lines.append(f"- reference-backed indel duplicated loci: {d['reference_backed_indel_duplicated_loci']} "
                 f"(revealed: {d['indel_duplicates_revealed']})")
    lines.append("")
    r = report.get("identity_route_demonstration") or {}
    lines.append("## Identity routes — SPDI / HGVS resolve to the canonical key (job1 task 3)")
    lines.append("")
    lines.append("A controlled example confirms that the new ClinVar-side identity tokens "
                 "(NCBI SPDI and genomic HGVS) resolve to the SAME canonical key as the "
                 "native coordinate locus, so a case carrying any one of them joins the "
                 "same record:")
    lines.append("")
    lines.append(f"- coordinate key: `{r.get('coordinate_key')}`")
    lines.append(f"- SPDI matches coordinate: {r.get('spdi_matches_coordinate')} "
                 f"(`{r.get('spdi_key')}`)")
    lines.append(f"- genomic HGVS matches coordinate: {r.get('hgvs_matches_coordinate')} "
                 f"(`{r.get('hgvs_g_key')}`)")
    lines.append(f"- indel SPDI key (reference-anchored): `{r.get('indel_spdi_key')}`")
    lines.append("")
    if not avail:
        lines.append("## To fill in the reference-backed columns")
        lines.append("")
        lines.append("```bash")
        lines.append("bash data/reference/install_grch38.sh        # install + record the FASTA")
        lines.append("../.venv/bin/python ingest/identity_audit_report.py   # re-run this audit")
        lines.append("```")
        lines.append("")
    lines.append("---")
    lines.append("*Generated by ingest/identity_audit_report.py.*")
    return "\n".join(lines) + "\n"


def main() -> int:
    report = build_report()
    os.makedirs(REPORTS, exist_ok=True)
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
        f.write("\n")
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write(render_markdown(report))

    for name, a in report["audits"].items():
        print(f"{name}: SNV={a['snv']} indel={a['indel']} "
              f"ref_backed={a['reference_available']} "
              f"free_indel_dups={a['reference_free']['indel']['duplicated_loci']}")
    print(f"Wrote {os.path.relpath(OUT_MD, ROOT)} and .json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
