"""Markdown renderers for the reviewer report and patient-safe summary.

The structured builders in :mod:`reporting.reviewer` / :mod:`reporting.summary`
are the source of truth; these functions are presentation only, turning a report
dict into a human-readable Markdown document for a reviewer console or an export.
"""

from __future__ import annotations

from typing import Any, Dict, List


def _fmt(value: Any) -> str:
    if value is None:
        return "—"
    return str(value)


def render_reviewer_markdown(report: Dict[str, Any]) -> str:
    lines: List[str] = []
    rel = report.get("release_status", {})
    ident = report.get("identity", {})
    clf = report.get("classification", {})

    lines.append("# Technical reviewer report")
    lines.append("")
    lines.append(f"**Release status:** {_fmt(rel.get('status'))}  ")
    if rel.get("signed_off_by"):
        lines.append(f"**Signed off by:** {_fmt(rel.get('signed_off_by'))} "
                     f"({_fmt(rel.get('signed_off_at'))})  ")
    lines.append(f"**Generated (UTC):** {_fmt(report.get('generated_utc'))}")
    lines.append("")

    lines.append("## Variant identity")
    lines.append("")
    for k, v in ident.items():
        lines.append(f"- **{k}:** {_fmt(v)}")
    lines.append("")

    lines.append("## Classification")
    lines.append("")
    lines.append(f"- **Tier:** {_fmt(clf.get('tier'))}")
    lines.append(f"- **Total points:** {_fmt(clf.get('total_points'))}")
    lines.append(f"- **Engine version:** {_fmt(clf.get('engine_version'))}")
    lines.append(f"- **Reconstruction hash:** `{_fmt(clf.get('reconstruction_hash'))}`")
    overrides = clf.get("overrides") or []
    if overrides:
        lines.append("- **Overrides:**")
        for o in overrides:
            lines.append(f"  - {_fmt(o)}")
    lines.append("")

    lines.append("## Evidence by source")
    lines.append("")
    ebs = report.get("evidence_by_source", {})
    if not ebs:
        lines.append("_No standardized evidence events were resolved._")
        lines.append("")
    for source, events in ebs.items():
        lines.append(f"### {source}")
        lines.append("")
        lines.append("| Criterion | Direction | Strength | Points | Version |")
        lines.append("|---|---|---|---|---|")
        for ev in events:
            lines.append(
                f"| {_fmt(ev.get('acmg_criterion'))} | {_fmt(ev.get('evidence_direction'))} "
                f"| {_fmt(ev.get('applied_strength'))} | {_fmt(ev.get('points'))} "
                f"| {_fmt(ev.get('source_version'))} |"
            )
        lines.append("")

    lines.append("## Contributions (criterion audit)")
    lines.append("")
    lines.append("| Criterion | Direction | Strength | Points | Source | Version | Warnings |")
    lines.append("|---|---|---|---|---|---|---|")
    for row in report.get("criteria", []):
        warns = ", ".join(row.get("warnings") or []) or "—"
        lines.append(
            f"| {_fmt(row.get('criterion'))} | {_fmt(row.get('direction'))} "
            f"| {_fmt(row.get('strength'))} | {_fmt(row.get('points'))} "
            f"| {_fmt(row.get('source'))} | {_fmt(row.get('source_version'))} | {warns} |"
        )
    lines.append("")

    prov = report.get("evidence_provenance", {})
    lines.append("## Evidence provenance")
    lines.append("")
    pv = prov.get("provider_versions", {})
    lines.append("- **Provider versions:** "
                 + (", ".join(f"{k}={v}" for k, v in pv.items()) or "—"))
    lines.append("- **Warnings:** " + (", ".join(prov.get("warnings", [])) or "—"))
    lines.append(f"- **Source records:** {len(prov.get('source_records', []))}")
    lines.append("")

    hist = report.get("history", {})
    lines.append("## History")
    lines.append("")
    lines.append(f"- **Previous classifications:** {len(hist.get('previous_classifications', []))}")
    lines.append(f"- **Reanalysis events:** {len(hist.get('reanalysis_events', []))}")
    lines.append(f"- **Tier-crossing alerts:** {len(hist.get('alerts', []))}")
    lines.append("")

    audit = report.get("audit", {})
    lines.append("## Audit trail")
    lines.append("")
    lines.append(f"- **Same-tier evidence changes (no alert):** {len(audit.get('same_tier_changes', []))}")
    lines.append(f"- **Tier crossings:** {len(audit.get('tier_crossings', []))}")
    if audit.get("note"):
        lines.append(f"- _{audit['note']}_")
    lines.append("")

    lines.append("## Limitations")
    lines.append("")
    for lim in report.get("limitations", []):
        lines.append(f"- {lim}")
    lines.append("")
    return "\n".join(lines) + "\n"


def render_patient_summary_markdown(report: Dict[str, Any]) -> str:
    lines: List[str] = []
    rel = report.get("release_status", {})
    result = report.get("result", {})

    lines.append("# Variant classification summary")
    lines.append("")
    lines.append(f"**Status:** {_fmt(rel.get('status'))}")
    lines.append("")
    lines.append("## Result")
    lines.append("")
    lines.append(f"**Classification:** {_fmt(result.get('classification'))}")
    lines.append("")
    lines.append(_fmt(result.get("plain_language")))
    lines.append("")
    lines.append("## What this means")
    lines.append("")
    lines.append(_fmt(report.get("what_this_means")))
    lines.append("")
    lines.append(_fmt(report.get("review_status")))
    lines.append("")
    lines.append("## Next steps")
    lines.append("")
    lines.append(_fmt(report.get("next_steps")))
    lines.append("")
    lines.append("## Limitations")
    lines.append("")
    for lim in report.get("limitations", []):
        lines.append(f"- {lim}")
    lines.append("")
    return "\n".join(lines) + "\n"
