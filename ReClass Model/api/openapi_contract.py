"""Pinned OpenAPI contract helpers.

The API contract is generated from the live FastAPI app and committed as
``api/openapi.json``. Tests compare the live schema to that pinned artifact so an
endpoint or schema change fails loudly until the artifact is intentionally
regenerated.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

OPENAPI_ARTIFACT = Path(__file__).with_name("openapi.json")


def stable_json(data: Dict[str, Any]) -> str:
    """Canonical JSON bytes for contract pinning."""
    return json.dumps(data, indent=2, sort_keys=True) + "\n"


def load_pinned_openapi(path: Path = OPENAPI_ARTIFACT) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_pinned_openapi(app, path: Path = OPENAPI_ARTIFACT) -> None:
    """Regenerate the pinned OpenAPI artifact from ``app``."""
    path.write_text(stable_json(app.openapi()), encoding="utf-8")
