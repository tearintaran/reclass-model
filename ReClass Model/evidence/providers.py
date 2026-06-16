"""Evidence-provider interface.

A provider turns *one* source (ClinGen ERepo, gnomAD, REVEL, a cohort, ...) into a
standardized :class:`~evidence.model.EvidenceBundle`. Keeping the contract this
narrow is what lets the scoring engine stay pure: providers own all the I/O,
identity matching, and provenance; the engine only sums the events a provider
emits.

Contract:

    EvidenceProvider.fetch(case_or_variant) -> EvidenceBundle

`fetch` MUST be deterministic for a fixed source snapshot: the same input and the
same underlying data must always yield the same events, warnings, and match block.
It must never raise on a simple "no evidence found"; it returns an empty-but-valid
bundle (with a warning) instead, so the caller can treat absence as a first-class,
auditable outcome rather than an error.
"""

from __future__ import annotations

from typing import Any

from .model import EvidenceBundle


class EvidenceProvider:
    """Base class for evidence providers. Subclasses implement :meth:`fetch`."""

    #: Stable provider identifier, e.g. ``"clingen_erepo"``.
    name: str = "provider"
    #: Stable source/provider version string, e.g. ``"ERepo"``.
    version: str = "0"

    def fetch(self, case_or_variant: Any) -> EvidenceBundle:
        """Resolve evidence for a case/variant into an :class:`EvidenceBundle`."""
        raise NotImplementedError(
            f"{type(self).__name__} must implement fetch(case_or_variant)."
        )

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return f"<{type(self).__name__} name={self.name!r} version={self.version!r}>"
