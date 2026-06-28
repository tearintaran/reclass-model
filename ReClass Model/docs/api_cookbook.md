# API Cookbook

The executable examples live in `api/cookbook_examples.py`. They accept a
TestClient/httpx-style client plus request headers and exercise the public flows:

- `evidence_resolution_flow`
- `classify_flow`
- `sign_off_flow`
- `report_flow`
- `reanalysis_flow`
- `alert_flow`
- `webhook_flow`

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

## Generated Python Client

The pinned OpenAPI artifact can generate the stdlib Python client in
`api/generated_client.py`:

```bash
python -c "from api.openapi_contract import generate_python_client; generate_python_client()"
```

Minimal use:

```python
from api.generated_client import ReClassClient

client = ReClassClient(
    "https://reclass.example",
    bearer_token="OIDC_RS256_TOKEN",
)
response = client.call(
    "classify_variant_classify_post",
    json_body={"evidence": {"events": []}},
)
```

## Webhook Example

Register an endpoint:

```python
client.post(
    "/webhooks/endpoints",
    json={
        "url": "https://customer.example/reclass/webhook",
        "secret": "replace-with-a-long-shared-secret",
        "event_types": ["tier_crossing", "config_change"],
    },
    headers=headers,
)
```

Outbound jobs are signed with `X-ReClass-Signature` using HMAC-SHA256 over the
canonical JSON body and retried by `api.webhooks.deliver_due`.

## Worklist Example

Open and progress a de-identified case:

```python
case = client.post(
    "/worklist/cases",
    json={
        "accession": "LAB-2026-0001",
        "priority": "urgent",
        "specimen_id": "SPEC-1",
        "received_at": "2026-06-23T16:00:00Z",
    },
    headers=headers,
).json()

client.post(
    f"/worklist/cases/{case['case_id']}/transition",
    json={"to_status": "in_review", "note": "Assigned for evidence review"},
    headers=headers,
)
```

List and ordinary detail responses redact patient MRN, patient name, and
indication. Full case context requires `GET /worklist/cases/{case_id}?include_phi=true`
and the `case:read_phi` permission; that access is audited.
