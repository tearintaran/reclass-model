"""Fixture split manifests and anti-leakage guardrails for validation tooling."""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURES_DIR = os.path.join(HERE, "fixtures")

DEVELOPMENT = "development"
VALIDATION = "validation"
HOLDOUT = "holdout"

VALID_SPLITS = {DEVELOPMENT, VALIDATION, HOLDOUT}
MANIFEST_FILES = {
    DEVELOPMENT: "development_manifest.json",
    VALIDATION: "validation_manifest.json",
    HOLDOUT: "holdout_manifest.json",
}


class HoldoutFixtureError(RuntimeError):
    """Raised when tuning/calibration code attempts to use a holdout fixture."""


def _manifest_path(split: str, fixtures_dir: str | None = None) -> str:
    return os.path.join(fixtures_dir or FIXTURES_DIR, MANIFEST_FILES[split])


def _entry_name(entry: Any) -> str | None:
    if isinstance(entry, str):
        return os.path.splitext(os.path.basename(entry))[0]
    if isinstance(entry, dict):
        if entry.get("benchmark"):
            return str(entry["benchmark"])
        if entry.get("name"):
            return str(entry["name"])
        if entry.get("path"):
            return os.path.splitext(os.path.basename(str(entry["path"])))[0]
    return None


def load_split_manifests(fixtures_dir: str | None = None) -> dict[str, dict[str, Any]]:
    """Load all split manifests, returning empty manifests for missing files."""
    manifests: dict[str, dict[str, Any]] = {}
    for split in (DEVELOPMENT, VALIDATION, HOLDOUT):
        path = _manifest_path(split, fixtures_dir)
        if not os.path.exists(path):
            manifests[split] = {"split": split, "fixtures": []}
            continue
        with open(path, encoding="utf-8") as fh:
            manifest = json.load(fh)
        declared = manifest.get("split", split)
        if declared != split:
            raise ValueError(f"{path} declares split {declared!r}, expected {split!r}.")
        manifests[split] = manifest
    return manifests


def split_members(fixtures_dir: str | None = None) -> dict[str, set[str]]:
    """Return benchmark names by split from the manifest files."""
    members: dict[str, set[str]] = {split: set() for split in VALID_SPLITS}
    for split, manifest in load_split_manifests(fixtures_dir).items():
        for entry in manifest.get("fixtures", []) or []:
            name = _entry_name(entry)
            if name:
                members[split].add(name)
    return members


def assert_split_manifests_disjoint(fixtures_dir: str | None = None) -> None:
    """Fail loudly if development/validation manifests overlap with holdout."""
    members = split_members(fixtures_dir)
    holdout = members[HOLDOUT]
    overlaps = {
        DEVELOPMENT: sorted(members[DEVELOPMENT] & holdout),
        VALIDATION: sorted(members[VALIDATION] & holdout),
    }
    bad = {split: names for split, names in overlaps.items() if names}
    if bad:
        raise ValueError(f"Holdout fixture leakage in split manifests: {bad}")


def fixture_split(fixture_or_name: Any, fixtures_dir: str | None = None) -> str | None:
    """Resolve a benchmark fixture's split from metadata or manifests."""
    name = None
    if isinstance(fixture_or_name, dict):
        for key in ("fixture_split", "validation_split", "split"):
            split = fixture_or_name.get(key)
            if split in VALID_SPLITS:
                return split
        name = fixture_or_name.get("benchmark") or fixture_or_name.get("name")
    else:
        name = fixture_or_name

    if name is None:
        return None
    name = str(name)
    for split, names in split_members(fixtures_dir).items():
        if name in names:
            return split
    return None


def is_holdout(fixture_or_name: Any, fixtures_dir: str | None = None) -> bool:
    return fixture_split(fixture_or_name, fixtures_dir) == HOLDOUT


def assert_not_holdout(
    fixture_or_name: Any,
    *,
    purpose: str,
    fixtures_dir: str | None = None,
) -> None:
    """Guard calibration/threshold code from tuning against holdout fixtures."""
    split = fixture_split(fixture_or_name, fixtures_dir)
    if split == HOLDOUT:
        if isinstance(fixture_or_name, dict):
            name = fixture_or_name.get("benchmark") or fixture_or_name.get("name") or "<unnamed>"
        else:
            name = str(fixture_or_name)
        raise HoldoutFixtureError(
            f"Refusing to use holdout fixture {name!r} for {purpose}; "
            "holdout data may be evaluated only after thresholds are locked."
        )


# ---------------------------------------------------------------------------
# Case-level holdout partition (blinded held-out evaluation)
# ---------------------------------------------------------------------------
#
# The fixture-level manifests above reserve whole fixtures. The functions below
# add a finer, per-case partition that carves a locked HOLDOUT sub-split out of a
# real validation benchmark, leaving the remaining DEVELOPMENT sub-split for
# tuning/calibration. Held-out concordance measured on the reserved sub-split is
# an unbiased estimate because calibration is forbidden from seeing it.
#
# Design commitments (see validation/preregistration.md):
#   * Deterministic -- SHA-256 of a salted identity string; no RNG, no wall
#     clock, so the partition is byte-reproducible and citable.
#   * Blind -- the identity is derived ONLY from the variant's genomic locus (or
#     stable id), never from the expected label or any engine output, so split
#     membership cannot be correlated with the outcome being measured.
#   * Cross-fixture-consistent -- keyed on the GRCh38 genomic locus, so the same
#     physical variant lands in the same sub-split in EVERY fixture it appears
#     in; no test variant can have influenced a threshold via another fixture.

HOLDOUT_PARTITION_SALT = "reclass-holdout-partition-v1"
HOLDOUT_PARTITION_FRACTION = 0.30
_PARTITION_RESOLUTION = 1_000_000


def case_identity_key(case: Any) -> str:
    """Canonical, label-blind identity for a fixture case.

    Prefers the GRCh38 genomic locus (``chrom-pos-ref-alt``) so the same physical
    variant is identified identically across fixtures; falls back to the stable
    case ``id`` when the locus is incomplete. Never reads the expected label.
    """
    locus = case.get("locus") if isinstance(case, dict) else None
    if isinstance(locus, dict):
        chrom, pos, ref, alt = (
            locus.get("chrom"),
            locus.get("pos"),
            locus.get("ref"),
            locus.get("alt"),
        )
        if None not in (chrom, pos, ref, alt) and ref != "" and alt != "":
            return f"GRCH38-{chrom}-{pos}-{ref}-{alt}".upper()
    cid = case.get("id") if isinstance(case, dict) else None
    if cid:
        return f"ID:{cid}"
    raise ValueError("case has neither a complete locus nor an id; cannot partition.")


def case_partition(
    case: Any,
    *,
    salt: str = HOLDOUT_PARTITION_SALT,
    holdout_fraction: float = HOLDOUT_PARTITION_FRACTION,
) -> str:
    """Resolve a single case to :data:`DEVELOPMENT` or :data:`HOLDOUT`."""
    key = case_identity_key(case)
    digest = hashlib.sha256(f"{salt}:{key}".encode("utf-8")).hexdigest()
    bucket = int(digest[:12], 16) % _PARTITION_RESOLUTION
    threshold = int(round(holdout_fraction * _PARTITION_RESOLUTION))
    return HOLDOUT if bucket < threshold else DEVELOPMENT


def partition_cases(cases: list, **kwargs: Any) -> dict[str, list]:
    """Split a list of cases into ``{development: [...], holdout: [...]}``."""
    out: dict[str, list] = {DEVELOPMENT: [], HOLDOUT: []}
    for case in cases:
        out[case_partition(case, **kwargs)].append(case)
    return out


def development_cases(cases: list, **kwargs: Any) -> list:
    """The tuning-visible sub-split (everything not reserved as holdout)."""
    return [c for c in cases if case_partition(c, **kwargs) == DEVELOPMENT]


def holdout_cases(cases: list, **kwargs: Any) -> list:
    """The reserved, locked sub-split (never shown to calibration)."""
    return [c for c in cases if case_partition(c, **kwargs) == HOLDOUT]


def partition_fingerprint(cases: list, **kwargs: Any) -> dict[str, Any]:
    """SHA-256 over the sorted holdout identity keys.

    Cryptographically pins exactly which variants are reserved, so the locked
    holdout sub-split can be cited and re-verified without shipping the case data.
    """
    keys = sorted(case_identity_key(c) for c in holdout_cases(cases, **kwargs))
    digest = hashlib.sha256()
    for key in keys:
        digest.update(key.encode("utf-8"))
        digest.update(b"\n")
    return {"n_holdout": len(keys), "sha256": digest.hexdigest()}


def partitioned_benchmark_names(fixtures_dir: str | None = None) -> set[str]:
    """Real validation-split benchmarks that carry an internal holdout sub-split.

    Development-split (synthetic) and wholly-reserved holdout fixtures are never
    internally partitioned.
    """
    return set(split_members(fixtures_dir)[VALIDATION])
