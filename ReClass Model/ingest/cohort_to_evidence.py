"""Turn a case-control cohort into PS4 evidence + cohort-count records (job1 task 5).

PS4 ("significantly increased prevalence in affected individuals vs controls") is the
one ACMG criterion that is only auditable *with the cohort it came from*: the case and
control allele counts, the denominators they were observed against, and the resulting
odds ratio. This ingest step reads a small case-control cohort fixture and, per
variant, emits a provenance-rich :class:`~evidence.model.EvidenceBundle` whose:

  * ``events``         carry a PS4 :class:`~engine.scoring.EvidenceEvent` (at a strength
                       scaled by the odds ratio) when the enrichment is statistically
                       significant -- and nothing (an explicit no-call) when it is not,
  * ``cohort_counts``  always carry the PS4 **denominator** and the case/control counts
                       (job1 task 5), even on a no-call, so the cohort is preserved.

It produces the populated evidence-model field and stops there: transporting it
through storage / reanalysis / alerting is Job 3, and surfacing it in reviewer reports
is Job 2 (this job does not edit their files).

Cohort fixture shape (JSON)::

    {
      "cohort": "<cohort label>",
      "access_date": "2026-06-17",
      "source": "<curated case-control source>",
      "variants": [
        {"variant_key": "1-100-A-G", "gene": "GENE",
         "case_count": 40, "case_total": 100,
         "control_count": 5, "control_total": 100,
         "ci_low": 3.1, "p_value": 1e-6}
      ]
    }

The builder is pure and offline (no network, no wall clock); the access date is taken
from the fixture. The CLI writes the result under ``data/cache/providers/`` (a local,
regenerable, gitignored artifact -- never back into the committed fixtures).

Run from ``ReClass Model/``::

    ../.venv/bin/python ingest/cohort_to_evidence.py <cohort_fixture.json>
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, List, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from evidence.upstream import CaseControlAdapter  # noqa: E402

DEFAULT_OUT = os.path.join(ROOT, "data", "cache", "providers", "ps4_cohort_evidence.json")


def build_cohort_evidence(
    cohort: Dict[str, Any],
    *,
    access_date: Optional[str] = None,
) -> Dict[str, Any]:
    """Build PS4 evidence + cohort-count records for a case-control cohort (pure).

    Each variant is routed through :class:`evidence.upstream.CaseControlAdapter`, so
    the PS4 significance rule and the odds-ratio strength bins are applied exactly as
    in the evidence layer. ``access_date`` defaults to the cohort fixture's
    ``access_date`` so the build is deterministic and offline. Returns a JSON-ready
    dict; per-variant entries always carry the cohort counts (and PS4 denominator),
    plus the emitted events and a ``ps4_called`` flag.
    """
    cohort_label = cohort.get("cohort")
    access = access_date or cohort.get("access_date")
    source = cohort.get("source") or "case_control_cohort"
    adapter = CaseControlAdapter(access_date=access)

    records: List[Dict[str, Any]] = []
    ps4_called = 0
    for variant in cohort.get("variants", []):
        record = dict(variant)
        record.setdefault("cohort", cohort_label)
        record.setdefault("source", source)
        # Drive the adapter through a case wrapper so it reads our case_control block.
        bundle = adapter.fetch({"evidence": {"case_control": record},
                                "locus": _locus_of(variant)})
        counts = bundle.cohort_counts.to_dict() if bundle.cohort_counts else None
        events = [_event_dict(e) for e in bundle.events]
        if bundle.events:
            ps4_called += 1
        records.append({
            "variant_key": variant.get("variant_key"),
            "gene": variant.get("gene"),
            "ps4_called": bool(bundle.events),
            "status": (bundle.match or {}).get("status"),
            "cohort_counts": counts,
            "events": events,
            "warnings": list(bundle.warnings),
        })

    return {
        "cohort": cohort_label,
        "source": source,
        "access_date": access,
        "provider": "ps4_case_control",
        "total_variants": len(records),
        "ps4_called": ps4_called,
        "records": records,
    }


def _locus_of(variant: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Best-effort locus dict from a variant's ``variant_key`` (``chrom-pos-ref-alt``)."""
    key = variant.get("variant_key")
    if not key:
        return None
    parts = str(key).split("-")
    if len(parts) != 4:
        return None
    try:
        return {"chrom": parts[0], "pos": int(parts[1]), "ref": parts[2], "alt": parts[3]}
    except ValueError:
        return None


def _event_dict(event: Any) -> Dict[str, Any]:
    return {
        "source": event.source,
        "acmg_criterion": event.acmg_criterion,
        "evidence_direction": event.evidence_direction,
        "applied_strength": event.applied_strength,
        "source_version": event.source_version,
    }


def main(argv: Optional[List[str]] = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if not argv:
        print(__doc__)
        return 2
    fixture_path = argv[0]
    out_path = argv[1] if len(argv) > 1 else DEFAULT_OUT
    with open(fixture_path, encoding="utf-8") as f:
        cohort = json.load(f)

    result = build_cohort_evidence(cohort)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, sort_keys=True)
        f.write("\n")

    print(f"Wrote PS4 cohort evidence for {result['total_variants']} variants "
          f"-> {os.path.relpath(out_path, ROOT)}")
    print(f"  cohort:        {result['cohort']}")
    print(f"  PS4 called:    {result['ps4_called']} / {result['total_variants']}")
    print(f"  access date:   {result['access_date']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
