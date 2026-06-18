"""Fixture split manifests and anti-leakage guardrails for validation tooling."""

from __future__ import annotations

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
