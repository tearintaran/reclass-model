"""Human-review reporting for the reclassification workflow.

Two report builders turn an auditable classification receipt (plus its evidence
provenance and history) into review artifacts:

  * :mod:`reporting.reviewer` — a *technical reviewer* report letting a
    credentialed reviewer audit exactly why a tier was produced (identity,
    evidence grouped by source, per-criterion contributions with provenance and
    warnings, prior classifications, reanalysis history, and tier-crossing
    alerts) BEFORE sign-off.
  * :mod:`reporting.summary` — a *patient-safe* plain-language summary.

Both are decision support only. They state limitations, source versions,
warnings, and the draft-vs-signed release status, and they deliberately contain
no treatment directives or management recommendations.
"""

from __future__ import annotations

from .reviewer import build_reviewer_report
from .summary import build_patient_summary
from .render import render_reviewer_markdown, render_patient_summary_markdown

__all__ = [
    "build_reviewer_report",
    "build_patient_summary",
    "render_reviewer_markdown",
    "render_patient_summary_markdown",
]
