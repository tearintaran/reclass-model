# API Cookbook

The executable examples live in `api/cookbook_examples.py`. They accept a
TestClient/httpx-style client plus request headers and exercise the public flows:

- `evidence_resolution_flow`
- `classify_flow`
- `sign_off_flow`
- `report_flow`
- `reanalysis_flow`
- `alert_flow`

The contract test runs `run_all(client, headers)` against the in-memory test app,
so these snippets stay synchronized with the pinned OpenAPI schema.

Minimal local pattern:

```python
from fastapi.testclient import TestClient

from api.app import create_app
from api.cookbook_examples import run_all
from api.evidence_resolver import EvidenceResolver
from api.settings import Settings
from api.store import InMemoryClinicalStore

app = create_app(
    settings=Settings(
        environment="development",
        legacy_default_roles=("viewer", "reviewer", "operator", "admin"),
    ),
    store=InMemoryClinicalStore(),
    resolver=EvidenceResolver(),
)
client = TestClient(app)
results = run_all(client, {"X-Tenant-Id": "00000000-0000-4000-8000-000000000001"})
```
