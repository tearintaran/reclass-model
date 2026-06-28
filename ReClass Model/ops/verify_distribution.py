#!/usr/bin/env python3
"""Build and smoke-test the wheel outside the source tree.

Editable installs and Docker's ``COPY . .`` can hide missing packages and runtime
assets. This check installs the wheel into an isolated target directory, imports
the API from there, and verifies the files required by the reviewer UI and
database migration tooling.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REQUIRED_WHEEL_FILES = {
    "api/openapi.json",
    "db/schema.sql",
    "deploy/migrations/001_audit_log.sql",
    "deploy/migrations/006_worklist_cases.sql",
    "deploy/migrations/007_force_rls.sql",
    "frontend/app.js",
    "frontend/index.html",
    "frontend/styles.css",
    "worklist/case.py",
}


def _run(command: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> None:
    subprocess.run(command, cwd=cwd, env=env, check=True)


def verify_distribution() -> None:
    build_dir = ROOT / "build"
    build_dir_existed = build_dir.exists()

    with tempfile.TemporaryDirectory(prefix="reclass-dist-") as temp:
        temp_path = Path(temp)
        wheel_dir = temp_path / "wheel"
        target_dir = temp_path / "site"
        wheel_dir.mkdir()

        try:
            _run(
                [
                    sys.executable,
                    "-m",
                    "pip",
                    "wheel",
                    ".",
                    "--no-deps",
                    "--no-build-isolation",
                    "--wheel-dir",
                    str(wheel_dir),
                ],
                cwd=ROOT,
            )
        finally:
            if not build_dir_existed:
                shutil.rmtree(build_dir, ignore_errors=True)

        wheels = sorted(wheel_dir.glob("reclass-*.whl"))
        if len(wheels) != 1:
            raise RuntimeError(f"expected one ReClass wheel, found: {wheels}")
        wheel = wheels[0]

        with zipfile.ZipFile(wheel) as archive:
            names = set(archive.namelist())
            missing = sorted(REQUIRED_WHEEL_FILES - names)
            if missing:
                raise RuntimeError(f"wheel is missing runtime files: {missing}")

        _run(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--no-deps",
                "--target",
                str(target_dir),
                str(wheel),
            ],
            cwd=temp_path,
        )

        smoke_test = r"""
import importlib.metadata
import json
import os
import sys
from pathlib import Path

site = Path(sys.argv[1]).resolve()
source = Path(sys.argv[2]).resolve()
dependency_paths = [path for path in sys.argv[3].split(os.pathsep) if path]
stdlib_paths = [
    path for path in sys.path
    if path
    and "site-packages" not in path
    and "dist-packages" not in path
    and Path(path).resolve() != source
]
sys.path[:] = [str(site), *dependency_paths, *stdlib_paths]

from api.app import create_app
from api.evidence_resolver import EvidenceResolver
from api.settings import Settings
from api.store import InMemoryClinicalStore
from db import apply as db_apply
from reclass_version import __version__
from worklist.case import InMemoryWorklistStore

app = create_app(
    settings=Settings(environment="development"),
    store=InMemoryClinicalStore(),
    resolver=EvidenceResolver(),
    worklist_store=InMemoryWorklistStore(),
)
root = Path(importlib.metadata.distribution("reclass").locate_file("")).resolve()
assert importlib.metadata.version("reclass") == __version__
assert app.version == __version__
assert any(getattr(route, "path", "") == "/reviewer" for route in app.routes)
assert (root / "frontend" / "index.html").is_file()
assert db_apply.SCHEMA_PATH.is_file()
assert len(db_apply.discover_migrations()) >= 7
print(json.dumps({
    "distribution_version": __version__,
    "frontend": str(root / "frontend" / "index.html"),
    "migration_count": len(db_apply.discover_migrations()),
    "status": "ok",
}, sort_keys=True))
"""
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        dependency_paths = os.pathsep.join(
            path
            for path in sys.path
            if path and ("site-packages" in path or "dist-packages" in path)
        )
        _run(
            [
                sys.executable,
                "-S",
                "-c",
                smoke_test,
                str(target_dir),
                str(ROOT),
                dependency_paths,
            ],
            cwd=temp_path,
            env=env,
        )


if __name__ == "__main__":
    verify_distribution()
