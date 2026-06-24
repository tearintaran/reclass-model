"""Variant case worklist — the primary daily reviewer surface (product layer).

The deterministic engine and the service layer score and persist *individual*
classification receipts keyed on a de-identified ``variant_key``. A working
diagnostic lab, however, does not work one variant at a time: scientists and
reviewers work a **queue of cases** — an ordered specimen with an ordering
provider, a turnaround clock, an assignee, and a status that moves through a
pipeline (``draft -> in_review -> signed -> released``). Today only a flat draft
list exists; this package adds the case/order model and the worklist that sits
above it.

The case model deliberately layers a small amount of **PHI context** (patient
MRN/name, clinical indication) above the otherwise de-identified research domain.
That context is access-controlled and never leaves the worklist by default: list
views and the standard detail view are **de-identified** (PHI redacted), and the
full record is only returned to a caller holding the ``case:read_phi``
permission. See :func:`worklist.case.redact_phi`.
"""

from .case import (
    ALLOWED_TRANSITIONS,
    Case,
    CaseError,
    DbWorklistStore,
    InMemoryWorklistStore,
    PHI_FIELDS,
    PRIORITIES,
    SLA_TARGET_HOURS,
    STATUSES,
    WorklistStore,
    redact_phi,
)

__all__ = [
    "ALLOWED_TRANSITIONS",
    "Case",
    "CaseError",
    "DbWorklistStore",
    "InMemoryWorklistStore",
    "PHI_FIELDS",
    "PRIORITIES",
    "SLA_TARGET_HOURS",
    "STATUSES",
    "WorklistStore",
    "redact_phi",
]
