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
PYTHON_CLIENT_ARTIFACT = Path(__file__).with_name("generated_client.py")


def stable_json(data: Dict[str, Any]) -> str:
    """Canonical JSON bytes for contract pinning."""
    return json.dumps(data, indent=2, sort_keys=True) + "\n"


def load_pinned_openapi(path: Path = OPENAPI_ARTIFACT) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_pinned_openapi(app, path: Path = OPENAPI_ARTIFACT) -> None:
    """Regenerate the pinned OpenAPI artifact from ``app``."""
    path.write_text(stable_json(app.openapi()), encoding="utf-8")


def generate_python_client(
    *,
    openapi_path: Path = OPENAPI_ARTIFACT,
    output_path: Path = PYTHON_CLIENT_ARTIFACT,
) -> None:
    """Write the lightweight stdlib Python client from the pinned contract."""
    schema = load_pinned_openapi(openapi_path)
    operations = []
    for route, methods in sorted(schema.get("paths", {}).items()):
        for method, spec in sorted(methods.items()):
            operation_id = spec.get("operationId")
            if operation_id:
                operations.append((operation_id, method.upper(), route))
    operation_lines = "\n".join(
        f"    {operation_id!r}: (\n"
        f"        {method!r},\n"
        f"        {route!r},\n"
        "    ),"
        for operation_id, method, route in operations
    )
    output_path.write_text(
        '''"""Generated stdlib client for the pinned ReClass OpenAPI contract.

Regenerate with:
    python -c "from api.openapi_contract import generate_python_client; generate_python_client()"
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, Optional


OPERATIONS = {
'''
        + operation_lines
        + '''
}


@dataclass(frozen=True)
class ApiResponse:
    status_code: int
    body: Any
    headers: Dict[str, str]


class ReClassClient:
    def __init__(self, base_url: str, *, bearer_token: Optional[str] = None,
                 tenant_id: Optional[str] = None, timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.bearer_token = bearer_token
        self.tenant_id = tenant_id
        self.timeout = timeout

    def request(self, method: str, path: str, *, json_body: Any = None,
                headers: Optional[Dict[str, str]] = None) -> ApiResponse:
        body = None if json_body is None else json.dumps(json_body).encode("utf-8")
        request_headers = {"Accept": "application/json"}
        if body is not None:
            request_headers["Content-Type"] = "application/json"
        if self.bearer_token:
            request_headers["Authorization"] = f"Bearer {self.bearer_token}"
        if self.tenant_id:
            request_headers["X-Tenant-Id"] = self.tenant_id
        request_headers.update(headers or {})
        req = urllib.request.Request(
            self.base_url + path,
            data=body,
            method=method,
            headers=request_headers,
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310
                raw = resp.read().decode("utf-8")
                payload = json.loads(raw) if raw else None
                return ApiResponse(resp.status, payload, dict(resp.headers))
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8")
            try:
                payload = json.loads(raw) if raw else None
            except json.JSONDecodeError:
                payload = raw
            return ApiResponse(exc.code, payload, dict(exc.headers))

    def call(self, operation_id: str, *, path_params: Optional[Dict[str, str]] = None,
             json_body: Any = None) -> ApiResponse:
        method, path = OPERATIONS[operation_id]
        for key, value in (path_params or {}).items():
            path = path.replace("{" + key + "}", str(value))
        return self.request(method, path, json_body=json_body)
''',
        encoding="utf-8",
    )
