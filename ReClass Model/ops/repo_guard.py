#!/usr/bin/env python3
"""Commit guard: block prohibited files from entering source control (gap §6 task 3).

The companion to the project-root ``.gitignore``. ``.gitignore`` is the first line of
defense (git won't stage an ignored file), but it is easy to defeat with ``git add
-f`` and it does nothing until the repo is initialized. This guard is the *active*
check: it scans a set of paths and flags anything that must never be committed —

  * **large reference FASTA** (``*.fa`` / ``*.fasta`` / ``*.fai`` / ``*.2bit``),
  * **raw source archives** under ``data/raw/`` (VCF/zip/gz/... bulk downloads),
  * **provider caches** under ``data/cache/providers/`` (regenerable, sometimes
    embedding queried coordinates),
  * **private / clinical data** (anything under a ``data/private/`` path or matching
    PHI/MRN-shaped names — defense in depth; identified data lives only in
    PostgreSQL, never in the repo), and
  * any **oversized** blob above a byte threshold that isn't an allowlisted doc.

Pure logic (:func:`check_paths`) is import-only and stdlib-only so it is unit-tested
without git; the :func:`main` CLI wraps it as a git ``pre-commit`` hook:

    # .git/hooks/pre-commit
    exec "$REPO/.venv/bin/python" "ReClass Model/ops/repo_guard.py" --staged \
         --repo-root "$REPO"

or install it from the repository root:

    python "ReClass Model/ops/repo_guard.py" --install-hook

Exit code 0 = clean, 1 = at least one prohibited path (commit should be aborted).
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from typing import Iterable, List, Optional, Tuple

# Reason codes (stable; surfaced to the operator and asserted in tests).
LARGE_FASTA = "large_fasta"
RAW_ARCHIVE = "raw_archive"
PROVIDER_CACHE = "provider_cache"
PRIVATE_CLINICAL = "private_clinical"
OVERSIZED = "oversized"

DEFAULT_SIZE_LIMIT = 5 * 1024 * 1024  # 5 MiB
HOOK_REL_PATH = os.path.join(".git", "hooks", "pre-commit")

_FASTA_SUFFIXES = (".fa", ".fasta", ".fai", ".2bit")
_ARCHIVE_SUFFIXES = (
    ".zip", ".gz", ".bgz", ".tar", ".tgz", ".vcf", ".bcf", ".bam", ".cram",
)
# Paths/names that always pass (small text docs, intentionally tracked).
_ALLOW_SUFFIXES = (".md", ".md5", ".txt", ".rst")
_ALLOW_BASENAMES = (".gitignore", ".gitkeep", "README.md")
# Directories whose contents are committed on purpose (small benchmark snapshots),
# so they are exempt from the name/oversized rules. See docs/data_governance.md.
_COMMITTED_DIRS = ("validation/fixtures/",)


def _norm(path: str) -> str:
    """Normalize a path to forward slashes for stable, OS-independent matching."""
    return str(path).replace(os.sep, "/")


def _is_allowlisted(path: str) -> bool:
    norm = _norm(path)
    base = norm.rsplit("/", 1)[-1]
    if base in _ALLOW_BASENAMES:
        return True
    if norm.lower().endswith(_ALLOW_SUFFIXES):
        return True
    return any(d in norm for d in _COMMITTED_DIRS)


def _classify(path: str, *, repo_root: str, size_limit: int) -> Optional[str]:
    """Return a reason code if ``path`` is prohibited, else ``None``."""
    norm = _norm(path)
    low = norm.lower()

    # Private/clinical data is prohibited regardless of size or extension.
    if "/data/private/" in low or low.startswith("data/private/"):
        return PRIVATE_CLINICAL
    base = low.rsplit("/", 1)[-1]
    if low.endswith((".phi", ".phi.json")) or "_patient" in base or "_mrn" in base:
        return PRIVATE_CLINICAL

    if _is_allowlisted(path):
        return None

    if low.endswith(_FASTA_SUFFIXES):
        return LARGE_FASTA

    in_raw = "/data/raw/" in low or low.startswith("data/raw/")
    if low.endswith(_ARCHIVE_SUFFIXES) or (in_raw and not _is_allowlisted(path)):
        return RAW_ARCHIVE

    if "/data/cache/providers/" in low or low.startswith("data/cache/providers/"):
        return PROVIDER_CACHE

    # Size-based catch-all for anything that slipped past the name rules.
    abspath = os.path.join(repo_root, path)
    try:
        if os.path.isfile(abspath) and os.path.getsize(abspath) > size_limit:
            return OVERSIZED
    except OSError:
        pass
    return None


def check_paths(paths: Iterable[str], *, repo_root: str = ".",
                size_limit: int = DEFAULT_SIZE_LIMIT) -> List[Tuple[str, str]]:
    """Return ``[(path, reason_code), ...]`` for every prohibited path.

    Deterministic and side-effect-free (other than reading file sizes for the
    oversized check), so it is safe to unit-test and to run in a pre-commit hook.
    """
    violations: List[Tuple[str, str]] = []
    for path in paths:
        if not path:
            continue
        reason = _classify(path, repo_root=repo_root, size_limit=size_limit)
        if reason is not None:
            violations.append((_norm(path), reason))
    return violations


def staged_paths(repo_root: str = ".") -> List[str]:
    """The list of paths git has staged (added) for the next commit."""
    out = subprocess.run(
        ["git", "-C", repo_root, "diff", "--cached", "--name-only", "--diff-filter=ACM"],
        capture_output=True, text=True, check=True,
    )
    return [line for line in out.stdout.splitlines() if line.strip()]


def hook_script(*, python_executable: str = ".venv/bin/python",
                model_dir: str = "ReClass Model") -> str:
    """Return the reproducible pre-commit hook body for this repository."""
    guard_path = os.path.join(model_dir, "ops", "repo_guard.py")
    return "\n".join([
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        'REPO="$(git rev-parse --show-toplevel)"',
        f'exec "$REPO/{python_executable}" "$REPO/{guard_path}" '
        '--staged --repo-root "$REPO"',
        "",
    ])


def install_pre_commit_hook(repo_root: str = ".",
                            *,
                            python_executable: str = ".venv/bin/python",
                            model_dir: str = "ReClass Model") -> str:
    """Install the repo guard as ``.git/hooks/pre-commit`` and return its path."""
    hooks_dir = os.path.join(repo_root, ".git", "hooks")
    if not os.path.isdir(hooks_dir):
        raise FileNotFoundError(f"git hooks directory not found: {hooks_dir}")
    hook_path = os.path.join(repo_root, HOOK_REL_PATH)
    with open(hook_path, "w", encoding="utf-8") as fh:
        fh.write(hook_script(python_executable=python_executable, model_dir=model_dir))
    os.chmod(hook_path, 0o755)
    return hook_path


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", help="paths to check (default: git staged)")
    parser.add_argument("--staged", action="store_true",
                        help="check git's staged files (use as a pre-commit hook)")
    parser.add_argument("--install-hook", action="store_true",
                        help="install .git/hooks/pre-commit for this repository")
    parser.add_argument("--repo-root", default=".", help="repository root")
    parser.add_argument("--size-limit", type=int, default=DEFAULT_SIZE_LIMIT,
                        help=f"oversized threshold in bytes (default {DEFAULT_SIZE_LIMIT})")
    args = parser.parse_args(argv)

    if args.install_hook:
        try:
            hook_path = install_pre_commit_hook(args.repo_root)
        except OSError as exc:
            print(f"[repo-guard] failed to install pre-commit hook: {exc}",
                  file=sys.stderr)
            return 1
        print(f"[repo-guard] installed pre-commit hook: {_norm(hook_path)}")
        return 0

    if args.staged or not args.paths:
        try:
            paths = staged_paths(args.repo_root)
        except (subprocess.CalledProcessError, FileNotFoundError):
            print("[repo-guard] not a git repository (or git unavailable); "
                  "nothing to check.", file=sys.stderr)
            return 0
    else:
        paths = args.paths

    violations = check_paths(paths, repo_root=args.repo_root, size_limit=args.size_limit)
    if not violations:
        return 0
    print("[repo-guard] refusing to commit prohibited files:", file=sys.stderr)
    for path, reason in violations:
        print(f"  {reason:<16} {path}", file=sys.stderr)
    print("\nThese are local/regenerable/private per ReClass Model/docs/"
          "data_governance.md. Remove them from the commit (and keep them ignored).",
          file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
