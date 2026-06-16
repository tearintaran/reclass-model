"""Configurable, multi-provider evidence resolution for the API.

The engine is pure; all I/O and identity matching live in ``evidence`` providers.
This resolver is a thin fan-out over a *registry* of those providers: it calls
each provider's ``fetch`` (which never raises on "no evidence") and merges the
returned bundles into one provenance-rich :class:`~evidence.model.EvidenceBundle`
plus a per-provider breakdown.

The registry is injected, so:

  * production can register the real ClinGen / REVEL / gnomAD providers, and
  * tests can register deterministic fakes to exercise match / absence / failure
    without any network or fixture files.

An empty registry is valid: it resolves to an empty bundle with a
``no_providers_configured`` warning, so the surface is always callable.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from evidence.model import EvidenceBundle
from evidence.providers import EvidenceProvider


class EvidenceResolver:
    """Fan a variant out across registered providers and merge the results."""

    def __init__(self, providers: Optional[Dict[str, EvidenceProvider]] = None) -> None:
        self._providers: Dict[str, EvidenceProvider] = dict(providers or {})

    # -- registry ----------------------------------------------------------- #
    def register(self, name: str, provider: EvidenceProvider) -> "EvidenceResolver":
        self._providers[name] = provider
        return self

    @property
    def provider_names(self) -> List[str]:
        return sorted(self._providers)

    # -- resolution --------------------------------------------------------- #
    def resolve(
        self,
        provider_input: Any,
        *,
        variant_key: Optional[str] = None,
        providers: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Resolve ``provider_input`` across the chosen providers.

        Returns ``{"bundle": EvidenceBundle, "per_provider": {name: EvidenceBundle}}``
        where ``bundle`` is the merged view (all events concatenated, provider
        versions/source records/warnings unioned) and ``per_provider`` keeps each
        provider's individual bundle for auditing. Unknown provider names are
        reported as a deterministic ``unknown_provider:<name>`` warning rather
        than raising, so a typo never takes the endpoint down.
        """
        selected = self._select(providers)

        merged = EvidenceBundle(variant_key=variant_key)
        per_provider: Dict[str, EvidenceBundle] = {}

        if providers:
            for name in providers:
                if name not in self._providers:
                    merged.warnings.append(f"unknown_provider:{name}")

        if not selected:
            if not self._providers:
                merged.warnings.append("no_providers_configured")
            elif not providers:
                merged.warnings.append("no_providers_selected")
            return {"bundle": merged, "per_provider": per_provider}

        for name in selected:
            provider = self._providers[name]
            bundle = provider.fetch(provider_input)
            per_provider[name] = bundle

            merged.events.extend(bundle.events)
            merged.provider_versions.update(bundle.provider_versions)
            merged.source_records.extend(bundle.source_records)
            for w in bundle.warnings:
                merged.warnings.append(f"{name}:{w}")
            if merged.variant_key is None:
                merged.variant_key = bundle.variant_key

        # Per-provider match blocks are kept in `match` under each provider name so
        # the merged bundle still answers "how did each source resolve identity?".
        merged.match = {
            name: bundle.match for name, bundle in per_provider.items()
        }
        return {"bundle": merged, "per_provider": per_provider}

    def _select(self, providers: Optional[List[str]]) -> List[str]:
        if providers is None:
            return self.provider_names
        return [p for p in providers if p in self._providers]
