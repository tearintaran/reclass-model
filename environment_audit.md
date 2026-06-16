# Environment Audit

Last updated: 2026-06-16, America/Phoenix.

This document records the current Python, pip, terminal, Homebrew, and project
validation state after the environment refresh and the completed evidence,
validation, API/reporting, storage, operations, governance, and reanalysis
verification pass.

## Scope

Checked project dependency manifests and developer tooling:

- `ReClass Model/requirements.txt`
- `ReClass Model/api/requirements.txt`
- `.vscode/settings.json`
- Project virtual environment at `.venv/`
- Shell startup files for bash and zsh
- Homebrew, Python, pip, PostgreSQL, Git, and Apple Command Line Tools

No `pyproject.toml`, `setup.py`, `Pipfile`, `poetry.lock`, `package.json`,
`go.mod`, `Cargo.toml`, or other package-manager manifests are present in this
repo snapshot.

This project folder is not currently inside a Git repository, so status review was
done by filesystem inspection rather than `git status`.

## External Version Checks

Current upstream versions checked during the environment audit:

- Python: 3.14.6 from `https://www.python.org/downloads/`
- pip: 26.1.2 from `https://pypi.org/project/pip/`
- psycopg-binary: 3.3.4
- matplotlib: 3.11.0
- FastAPI: 0.137.1
- uvicorn: 0.49.0
- pytest: 9.1.0

`ReClass Model/requirements.txt` pins the active storage dependency
(`psycopg[binary]`) and diagnostic plotting (`matplotlib`). The API component
keeps FastAPI/uvicorn/httpx in `ReClass Model/api/requirements.txt`.

## Project Python Environment

The project virtual environment is on the checked Python:

- `.venv/bin/python`: Python 3.14.6
- `.venv/bin/pip`: pip 26.1.2
- `python -m pip check`: clean
- `python -m pip list --outdated`: clean

Installed packages in the active venv:

| Package | Version | Role |
|---|---:|---|
| `annotated-doc` | 0.0.4 | FastAPI/Pydantic dependency |
| `annotated-types` | 0.7.0 | Pydantic dependency |
| `anyio` | 4.14.0 | Starlette/FastAPI async dependency |
| `certifi` | 2026.5.20 | HTTP client certificates |
| `click` | 8.4.1 | uvicorn CLI dependency |
| `contourpy` | 1.3.3 | matplotlib dependency |
| `cycler` | 0.12.1 | matplotlib dependency |
| `fastapi` | 0.137.1 | API service |
| `fonttools` | 4.63.0 | matplotlib dependency |
| `h11` | 0.16.0 | HTTP protocol dependency |
| `httpcore` | 1.0.9 | httpx dependency |
| `httptools` | 0.8.0 | uvicorn optional dependency |
| `httpx` | 0.28.1 | API tests / Starlette TestClient |
| `idna` | 3.18 | HTTP dependency |
| `kiwisolver` | 1.5.0 | matplotlib dependency |
| `matplotlib` | 3.11.0 | diagnostic plots |
| `numpy` | 2.4.6 | matplotlib dependency |
| `packaging` | 26.2 | packaging utilities |
| `pillow` | 12.2.0 | matplotlib image dependency |
| `pip` | 26.1.2 | package manager |
| `psycopg` | 3.3.4 | PostgreSQL storage layer/tests |
| `psycopg-binary` | 3.3.4 | PostgreSQL binary package |
| `pydantic` | 2.13.4 | API schemas |
| `pydantic_core` | 2.46.4 | Pydantic dependency |
| `pyparsing` | 3.3.2 | matplotlib dependency |
| `python-dateutil` | 2.9.0.post0 | matplotlib dependency |
| `python-dotenv` | 1.2.2 | uvicorn optional dependency |
| `PyYAML` | 6.0.3 | uvicorn optional dependency |
| `setuptools` | 82.0.1 | packaging/build support |
| `six` | 1.17.0 | dateutil dependency |
| `starlette` | 1.3.1 | FastAPI dependency |
| `typing_extensions` | 4.15.0 | Pydantic/FastAPI dependency |
| `typing-inspection` | 0.4.2 | Pydantic dependency |
| `uvicorn` | 0.49.0 | ASGI server |
| `uvloop` | 0.22.1 | uvicorn optional dependency |
| `watchfiles` | 1.2.0 | uvicorn reload dependency |
| `websockets` | 16.0 | uvicorn optional dependency |
| `wheel` | 0.47.0 | packaging/build support |

The prior Python 3.10 virtual environment was preserved as
`.venv-py310-backup-20260615/`.

## Terminal Configuration

VS Code is configured to use and activate the project venv:

- `python.defaultInterpreterPath`: `${workspaceFolder}/.venv/bin/python`
- `python.terminal.activateEnvironment`: `true`
- `terminal.integrated.cwd`: `${workspaceFolder}`

Shell startup was corrected so raw terminals no longer prefer old python.org
framework installs:

- `~/.bash_profile` now sources `~/.bashrc`
- `~/.bashrc` prepends Homebrew PostgreSQL, Homebrew Python 3.14, and
  `/usr/local/bin`
- `~/.zshrc` prepends the same paths
- Backups were written to `~/.bash_profile.bak-20260615` and
  `~/.zshrc.bak-20260615`

Fresh bash and zsh shells resolve:

- `python`: Python 3.14.6
- `python3`: Python 3.14.6
- `pip`: pip 26.1.2
- `pip3`: pip 26.1.2

## Homebrew And System Tools

Homebrew was updated from 4.5.6 to 6.0.2. The local `homebrew/core` tap was
untapped after Homebrew moved back to the JSON API path.

Current relevant installed tools:

- Homebrew: 6.0.2
- Python: `python@3.14 3.14.6`
- `python-packaging 26.2`
- PostgreSQL: `postgresql@16 16.14`
- Git: 2.54.0
- Apple clang: 21.0.0 from Command Line Tools for Xcode 26.5

No outdated Homebrew formulae were reported by `brew outdated --formula --quiet`
during the environment audit.

Obsolete Homebrew kegs were removed:

- `pcre 8.45`
- `python@3.8 3.8.2`
- `openssl@1.1 1.1.1g`

Homebrew also autoremoved formulae that were no longer depended on after the
obsolete kegs were removed, including `cmake`, `ninja`, `swig`, `z3`, and
`python@3.13`. They are not required by the current repo snapshot.

Apple Command Line Tools were installed through Software Update:

- Package: Command Line Tools for Xcode 26.5
- Receipt: `com.apple.pkg.CLTools_Executables 26.5.0.0.1777544298`
- `xcrun --find clang`: `/Library/Developer/CommandLineTools/usr/bin/clang`
- A minimal C compile smoke test passed.

## Other Python Installs

The old python.org framework installs are no longer preferred by shell `PATH`, but
their package sets were also updated and checked:

- Python.framework 3.10: pip 26.1.2, no broken requirements, no outdated packages
- Python.framework 3.13: pip 26.1.2, no broken requirements, no outdated packages

## Project Validation

Validated from `ReClass Model/` with the rebuilt `.venv`:

```bash
../.venv/bin/python -m unittest discover -s tests -v
../.venv/bin/python evidence/enrich_clinvar.py
../.venv/bin/python validation/harness.py
../.venv/bin/python validation/harness.py clingen_real_v1
../.venv/bin/python validation/harness.py clinvar_real_v1
../.venv/bin/python validation/harness.py clinvar_enriched_v1
../.venv/bin/python validation/analyze_failures.py clinvar_enriched_v1
../.venv/bin/python validation/compare_reports.py clinvar_real_v1 clinvar_enriched_v1
../.venv/bin/python validation/calibration.py clingen_real_v1
../.venv/bin/python validation/calibration.py clinvar_enriched_v1
../.venv/bin/python -m engine.reference_cache --status
../.venv/bin/python db/apply.py
../.venv/bin/python validation/plots.py
```

Results:

- Unit/integration suite: 389 tests, all passing.
- Synthetic benchmark: GATE PASS, 90.5% definitive concordance, 0 serious
  discordances, 92.0% overall exact concordance.
- ClinGen real benchmark: GATE PASS, 94.7% definitive concordance, 4 serious
  discordances, 93.0% overall exact concordance.
- ClinVar raw benchmark: GATE FAIL as expected, 5.0% definitive concordance, 34
  serious discordances, 19.9% overall exact concordance.
- ClinVar enriched benchmark: GATE FAIL as expected, but improved to 37.8%
  definitive concordance, 9 serious discordances, 43.3% overall exact concordance.
- Raw vs enriched comparison: definitive concordance +32.8 percentage points,
  overall exact concordance +23.5 percentage points, serious discordance count -25.
- Reference cache status helper: works and reports the default GRCh38 FASTA as
  missing until a local FASTA is supplied.
- Calibration reports: generated for ClinGen real and enriched ClinVar, including
  low-performing groups, serious-discordance triage, and threshold sensitivity.
- REVEL and gnomAD provider tests: pass offline with mocked/local data.
- API/reporting tests: pass without requiring a live database.
- Storage/ops/reanalysis tests: pass against PostgreSQL in the current environment.
- Schema apply: succeeds against the local `reclass_dev` PostgreSQL database.
- Diagnostic plot generation: works and writes PNG diagnostics.

## Remaining Environment Warnings

`brew doctor` still reports two categories of warnings:

- Unbrewed Tcl/Tk dylibs, headers, pkg-config files, and static libraries under
  `/usr/local`. These appear to be non-Homebrew files and were not removed
  automatically because they may belong to another local installer.
- Homebrew is installed under `/usr/local` on this Apple Silicon machine.
  Homebrew considers this a Tier 3 configuration; the default prefix would be
  `/opt/homebrew`. Fixing this requires a Homebrew migration/reinstall, not a
  project-local update.

These warnings do not currently block the repo tests, Python environment,
PostgreSQL client, Git, pip, compiler smoke test, validation harness, or diagnostic
plot generation.

## Future Verification Commands

From the repo root:

```bash
source .venv/bin/activate
python --version
pip --version
python -m pip check
python -m pip list --outdated
```

From `ReClass Model/`:

```bash
../.venv/bin/python -m unittest discover -s tests -v
../.venv/bin/python validation/harness.py
../.venv/bin/python validation/harness.py clingen_real_v1
../.venv/bin/python validation/harness.py clinvar_real_v1
../.venv/bin/python validation/harness.py clinvar_enriched_v1
../.venv/bin/python validation/compare_reports.py clinvar_real_v1 clinvar_enriched_v1
../.venv/bin/python validation/calibration.py clingen_real_v1
../.venv/bin/python -m engine.reference_cache --status
```
