"""Versioned, auditable scoring-configuration registry.

The point model the engine sums is a *clinical* artifact: point values, tier
cutoffs, REVEL bins, and allele-frequency thresholds all encode ClinGen SVI / VCEP
judgement that must be reviewable and explicitly versioned, not buried as module
constants. This registry makes the configuration:

  * **file-backed and versioned** -- each config is a JSON document under
    ``engine/configs/`` carrying its ``version`` and provenance notes, so a change
    is a reviewable diff, not an edit to Python;
  * **fingerprinted** -- every config exposes a deterministic ``config_hash`` over
    its scoring-relevant fields and an ``engine_version`` that *changes when the
    scoring config changes*, so config-relevant changes alter reconstruction hashes
    (acceptance criterion B);
  * **layerable** -- gene / disease / VCEP-specific BA1/BS1/PM2/PP3/BP4 deviations
    and founder-variant frequency exceptions live in an ``overrides`` block and are
    applied by :meth:`EngineConfig.resolve` *without ad hoc code changes*.

``engine.config`` re-exports the base config's values as module constants so the
existing public surface (``STRENGTH_POINTS``, ``points_to_tier``, ``REVEL_PP3`` ...)
is unchanged and the scoring engine and its tests keep working byte-for-byte.

PS4 cohort thresholds are intentionally NOT represented here; they live in
``monitoring/reanalysis.py`` with the reanalysis/cohort logic.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field, replace
from typing import Any, Dict, List, Optional, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
CONFIGS_DIR = os.path.join(_HERE, "configs")

#: The version loaded as the engine default (re-exported by ``engine.config``).
BASE_CONFIG_VERSION = "1.0.0"

Bin = Tuple[float, str]


# --------------------------------------------------------------------------- #
# Config object                                                               #
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class EngineConfig:
    """One immutable, versioned scoring configuration.

    The scoring-relevant fields (``strength_points`` ... ``pm2_af``) are exactly
    what :func:`config_hash` fingerprints; ``label``/``description``/``provenance``/
    ``overrides`` are audit metadata that do not change a score and are excluded
    from the hash.
    """

    version: str
    strength_points: Dict[str, int]
    tier_cutoffs: Tuple[Bin, ...]          # evaluated high -> low
    tier_default: str
    revel_pp3: Tuple[Bin, ...]             # high -> low
    revel_bp4: Tuple[Bin, ...]             # low -> high
    ba1_af: float
    bs1_af: float
    pm2_af: float
    overrides: Tuple[Dict[str, Any], ...] = ()
    label: str = ""
    description: str = ""
    provenance: Dict[str, Any] = field(default_factory=dict)
    #: True only for the unmodified, file-loaded base config. A resolved/perturbed
    #: config is never "base", which is what lets the base keep the bare version
    #: string (and historical reconstruction hashes) while any deviation gets a
    #: fingerprinted version.
    is_base: bool = False

    # -- tier mapping ------------------------------------------------------- #
    def points_to_tier(self, points: float) -> str:
        """Map a signed total-point score to an ACMG/AMP tier under THIS config."""
        for threshold, tier in self.tier_cutoffs:
            if points >= threshold:
                return tier
        return self.tier_default

    # -- fingerprint / version --------------------------------------------- #
    def scoring_fields(self) -> Dict[str, Any]:
        """The scoring-relevant subset that defines this config's identity."""
        return {
            "strength_points": dict(sorted(self.strength_points.items())),
            "tier_cutoffs": [list(b) for b in self.tier_cutoffs],
            "tier_default": self.tier_default,
            "revel_pp3": [list(b) for b in self.revel_pp3],
            "revel_bp4": [list(b) for b in self.revel_bp4],
            "ba1_af": self.ba1_af,
            "bs1_af": self.bs1_af,
            "pm2_af": self.pm2_af,
        }

    @property
    def config_hash(self) -> str:
        """Deterministic SHA-256 over the scoring-relevant fields (lowercase hex)."""
        payload = json.dumps(self.scoring_fields(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @property
    def engine_version(self) -> str:
        """Reconstruction-safe engine/config version string.

        The unmodified base config keeps the bare ``version`` so historical
        classifications reconstruct byte-for-byte. Any scoring-relevant deviation
        (a resolved override or a perturbation) yields a *distinct, fingerprinted*
        version, so a config-relevant change provably alters reconstruction hashes.
        """
        if self.is_base:
            return self.version
        return f"{self.version}+cfg.{self.config_hash[:12]}"

    # -- override resolution ------------------------------------------------ #
    def matching_overrides(
        self,
        *,
        gene: Optional[str] = None,
        disease: Optional[str] = None,
        vcep: Optional[str] = None,
        variant_key: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Overrides whose ``match`` block is fully satisfied by the selector.

        An override applies only when *every* field it names matches (case-insensitive
        for the string selectors); unspecified selector fields never match a named
        constraint, so a gene-specific rule never fires on a bare VCEP query.
        """
        selector = {
            "gene": _norm(gene),
            "disease": _norm(disease),
            "vcep": _norm(vcep),
            "variant_key": _norm(variant_key),
        }
        out: List[Dict[str, Any]] = []
        for ov in self.overrides:
            match = ov.get("match") or {}
            if not match:
                continue
            ok = True
            for k, v in match.items():
                if _norm(v) != selector.get(k):
                    ok = False
                    break
            if ok:
                out.append(ov)
        return out

    def resolve(
        self,
        *,
        gene: Optional[str] = None,
        disease: Optional[str] = None,
        vcep: Optional[str] = None,
        variant_key: Optional[str] = None,
    ) -> "ResolvedConfig":
        """Apply any matching gene/disease/VCEP/variant overrides.

        Returns a :class:`ResolvedConfig` wrapping the (possibly unchanged) config
        and the list of applied override ids. When no override matches, the base
        config is returned unchanged so its bare version/hash are preserved.
        """
        applied = self.matching_overrides(
            gene=gene, disease=disease, vcep=vcep, variant_key=variant_key
        )
        if not applied:
            return ResolvedConfig(self, [])

        ba1, bs1, pm2 = self.ba1_af, self.bs1_af, self.pm2_af
        revel_pp3 = self.revel_pp3
        revel_bp4 = self.revel_bp4
        cutoffs = self.tier_cutoffs
        tier_default = self.tier_default
        for ov in applied:
            s = ov.get("set") or {}
            ba1 = s.get("ba1_af", ba1)
            bs1 = s.get("bs1_af", bs1)
            pm2 = s.get("pm2_af", pm2)
            if "revel_pp3" in s:
                revel_pp3 = tuple(tuple(b) for b in s["revel_pp3"])
            if "revel_bp4" in s:
                revel_bp4 = tuple(tuple(b) for b in s["revel_bp4"])
            if "tier_cutoffs" in s:
                cutoffs = tuple(tuple(b) for b in s["tier_cutoffs"])
            if "tier_default" in s:
                tier_default = s["tier_default"]

        resolved = replace(
            self,
            ba1_af=ba1,
            bs1_af=bs1,
            pm2_af=pm2,
            revel_pp3=revel_pp3,
            revel_bp4=revel_bp4,
            tier_cutoffs=cutoffs,
            tier_default=tier_default,
            is_base=False,
        )
        return ResolvedConfig(resolved, [ov.get("id", "unnamed") for ov in applied])

    def perturb(
        self,
        *,
        tier_cutoffs: Optional[Tuple[Bin, ...]] = None,
        ba1_af: Optional[float] = None,
        bs1_af: Optional[float] = None,
        pm2_af: Optional[float] = None,
        revel_pp3: Optional[Tuple[Bin, ...]] = None,
        revel_bp4: Optional[Tuple[Bin, ...]] = None,
        version_suffix: Optional[str] = None,
    ) -> "EngineConfig":
        """Return a non-base copy with selected scoring fields replaced.

        Used by threshold-sensitivity analysis to score the same evidence under a
        perturbed configuration. The result is never ``is_base`` so its
        ``engine_version`` is fingerprinted.
        """
        version = self.version + (version_suffix or "")
        return replace(
            self,
            version=version,
            tier_cutoffs=tuple(tier_cutoffs) if tier_cutoffs is not None else self.tier_cutoffs,
            ba1_af=ba1_af if ba1_af is not None else self.ba1_af,
            bs1_af=bs1_af if bs1_af is not None else self.bs1_af,
            pm2_af=pm2_af if pm2_af is not None else self.pm2_af,
            revel_pp3=tuple(revel_pp3) if revel_pp3 is not None else self.revel_pp3,
            revel_bp4=tuple(revel_bp4) if revel_bp4 is not None else self.revel_bp4,
            is_base=False,
        )


@dataclass(frozen=True)
class ResolvedConfig:
    """An :class:`EngineConfig` plus the override ids that produced it."""

    config: EngineConfig
    applied_override_ids: List[str]

    @property
    def engine_version(self) -> str:
        return self.config.engine_version


# --------------------------------------------------------------------------- #
# Loading / registry                                                          #
# --------------------------------------------------------------------------- #
def _norm(value: Any) -> Optional[str]:
    if value is None:
        return None
    s = str(value).strip().lower()
    return s or None


def _as_bins(rows: Any) -> Tuple[Bin, ...]:
    return tuple((float(t), str(s)) for t, s in rows)


def config_from_dict(data: Dict[str, Any], *, is_base: bool = False) -> EngineConfig:
    """Build an :class:`EngineConfig` from a parsed config document."""
    freq = data.get("frequency") or {}
    return EngineConfig(
        version=str(data["version"]),
        strength_points={k: int(v) for k, v in (data["strength_points"]).items()},
        tier_cutoffs=_as_bins(data["tier_cutoffs"]),
        tier_default=str(data["tier_default"]),
        revel_pp3=_as_bins(data["revel_pp3"]),
        revel_bp4=_as_bins(data["revel_bp4"]),
        ba1_af=float(freq["ba1_af"]),
        bs1_af=float(freq["bs1_af"]),
        pm2_af=float(freq["pm2_af"]),
        overrides=tuple(data.get("overrides") or ()),
        label=str(data.get("label", "")),
        description=str(data.get("description", "")),
        provenance=dict(data.get("provenance") or {}),
        is_base=is_base,
    )


def _config_path(version: str) -> str:
    # base_v1.json holds version "1.0.0"; map "1.x.y" -> base_v<major>.json.
    major = version.split(".", 1)[0]
    return os.path.join(CONFIGS_DIR, f"base_v{major}.json")


def load_config(version: str = BASE_CONFIG_VERSION) -> EngineConfig:
    """Load a versioned config document. The base version is flagged ``is_base``."""
    path = _config_path(version)
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"No config file for version {version!r} (looked for {path})."
        )
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return config_from_dict(data, is_base=(data.get("version") == BASE_CONFIG_VERSION))


def available_versions() -> List[str]:
    """Versions discoverable under ``engine/configs/`` (sorted)."""
    out = []
    if os.path.isdir(CONFIGS_DIR):
        for name in os.listdir(CONFIGS_DIR):
            if name.startswith("base_v") and name.endswith(".json"):
                try:
                    with open(os.path.join(CONFIGS_DIR, name), encoding="utf-8") as f:
                        out.append(str(json.load(f).get("version")))
                except (OSError, ValueError):
                    continue
    return sorted(out)


# The default/base config, loaded once. ``engine.config`` re-exports its values.
BASE_CONFIG: EngineConfig = load_config(BASE_CONFIG_VERSION)


def get_config(version: Optional[str] = None) -> EngineConfig:
    """Return the base config (cached) or load a specific version."""
    if version is None or version == BASE_CONFIG_VERSION:
        return BASE_CONFIG
    return load_config(version)
