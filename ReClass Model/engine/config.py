"""Versioned, auditable configuration for the scoring engine.

The scoring constants below are no longer hand-edited here: they are the values of
the **base versioned config** (``engine/configs/base_v1.json``), loaded through
:mod:`engine.config_registry`. Moving point values, tier cutoffs, REVEL bins, and
allele-frequency thresholds into a versioned, file-backed registry (roadmap §2) is
what makes the point model clinically reviewable and explicitly versioned: a change
is now a reviewable config diff carrying provenance, and any scoring-relevant change
yields a distinct config fingerprint (see :func:`config_fingerprint`).

This module keeps the historical public surface stable for backward compatibility
and for callers in monitoring, API, validation, and storage:

  * ``ENGINE_VERSION`` -- the engine/config version string,
  * ``STRENGTH_POINTS`` -- ACMG/AMP Bayesian points per evidence strength,
  * ``points_to_tier`` -- signed-points -> tier mapping,
  * ``REVEL_PP3`` / ``REVEL_BP4`` -- calibrated REVEL bins,
  * ``BA1_AF`` / ``BS1_AF`` / ``PM2_AF`` -- popmax allele-frequency cutoffs.

Values follow the ClinGen SVI Bayesian points framework (Tavtigian et al. 2020) for
tier cutoffs and evidence-strength points, calibrated REVEL bins (Pejaver et al.
2022), and gnomAD popmax/FAF practice for the frequency cutoffs. Gene/disease/VCEP
deviations and founder-variant frequency exceptions live in the config's
``overrides`` block and are resolved via ``config_registry`` -- not patched here.

PS4 cohort thresholds are out of scope for this module; they live in
``monitoring/reanalysis.py``.
"""

from __future__ import annotations

from typing import List, Tuple

from .config_registry import BASE_CONFIG, EngineConfig, get_config  # noqa: F401

# The engine/config version. Bump (and add a new engine/configs/base_v<major>.json)
# when a config-relevant change should alter reconstruction hashes.
ENGINE_VERSION = BASE_CONFIG.version

# ACMG/AMP Bayesian point value for each evidence strength (Tavtigian 2020).
STRENGTH_POINTS = dict(BASE_CONFIG.strength_points)


def points_to_tier(points: float) -> str:
    """Map a signed total-point score to an ACMG/AMP tier (base config)."""
    return BASE_CONFIG.points_to_tier(points)


# REVEL -> PP3 (pathogenic). Evaluated highest threshold first.
REVEL_PP3: List[Tuple[float, str]] = [tuple(b) for b in BASE_CONFIG.revel_pp3]

# REVEL -> BP4 (benign). Evaluated lowest threshold first.
REVEL_BP4: List[Tuple[float, str]] = [tuple(b) for b in BASE_CONFIG.revel_bp4]

# gnomAD popmax allele-frequency cutoffs.
BA1_AF = BASE_CONFIG.ba1_af      # >= 5%   -> BA1 stand-alone benign
BS1_AF = BASE_CONFIG.bs1_af      # >= 1%   -> BS1 strong benign
PM2_AF = BASE_CONFIG.pm2_af      # <= 0.01% -> PM2 supporting pathogenic

# Deterministic fingerprint of the scoring-relevant config (audit/versioning).
CONFIG_HASH = BASE_CONFIG.config_hash


def config_fingerprint() -> dict:
    """A small, auditable summary of the active base config.

    Surfaces the version, the scoring-relevant fingerprint, and the available
    gene/disease/VCEP overrides so a reviewer can confirm what the engine is
    scoring with -- and so a config-relevant change is visibly reflected in the
    fingerprint (and therefore in reconstruction hashes via ``engine_version``).
    """
    return {
        "version": BASE_CONFIG.version,
        "engine_version": BASE_CONFIG.engine_version,
        "config_hash": BASE_CONFIG.config_hash,
        "label": BASE_CONFIG.label,
        "strength_points": dict(sorted(BASE_CONFIG.strength_points.items())),
        "tier_cutoffs": [list(b) for b in BASE_CONFIG.tier_cutoffs],
        "frequency": {"ba1_af": BA1_AF, "bs1_af": BS1_AF, "pm2_af": PM2_AF},
        "override_ids": [ov.get("id") for ov in BASE_CONFIG.overrides],
    }
