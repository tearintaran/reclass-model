# Authentication and authorization

ReClass production traffic uses **Bearer token authentication** with **role-based
access control (RBAC)**. Tenant isolation is enforced at two layers: the token
carries a `tenant_id`, and PostgreSQL row-level security scopes all clinical data.

## Authentication modes

| Environment | Behavior |
|---|---|
| `development` | Legacy `X-Tenant-Id` header accepted when no Bearer token is present. Grants configurable default roles (default: `reviewer`). |
| `staging` | Bearer token required unless you explicitly construct development settings for local tests. |
| `production` | **Bearer token required.** Environment-built settings default to `RECLASS_AUTH_MODE=oidc`, which accepts only RS256/JWKS OIDC tokens. `X-Tenant-Id` alone is rejected. |

## Bearer tokens

`RECLASS_AUTH_MODE` controls fallback behavior:

- `oidc`: accept only RS256/JWKS OIDC bearer tokens. HS256 JWT and API-key
  fallback are disabled even if `RECLASS_JWT_SECRET` or `RECLASS_API_KEYS` are
  set.
- `auto`: try OIDC first when configured, then HS256 JWT, then static API keys.
  Use this for development or tightly controlled non-production automation.

Three token types are available when the mode allows them:

### 1. OIDC / JWKS JWT (RS256)

Set `RECLASS_OIDC_ISSUER` and either `RECLASS_OIDC_JWKS_URL` or
`RECLASS_OIDC_JWKS`. Set `RECLASS_OIDC_AUDIENCE` when your identity provider
issues audience-scoped tokens.

Tokens must include `sub`, `tenant_id` (or `tid`), roles (`roles` or `role`), and
standard expiry claims. The verifier checks the RS256 signature against JWKS,
issuer, audience when configured, `exp`, and `nbf`; unknown key IDs trigger a
bounded JWKS refetch for key rotation.

**Tenant binding.** A validly-signed OIDC token may act only as the tenant it is
*bound* to: the request layer checks the token's `iss`/`aud` against the asserted
tenant's registered `oidc_issuer`/`oidc_audience` and rejects (403) a mismatch, or a
tenant with no registered OIDC config. Without this, a single shared IdP would let
any signed token set `tenant_id` to a victim tenant and cross the PHI boundary
(row-level security then *authorizes* the access because the claim was trusted).
Register each tenant's issuer/audience via the platform-operator admin API.

ES256 is intentionally not implemented in the dependency-free verifier. Use RS256
or add a vetted crypto dependency before accepting elliptic-curve tokens.

### 2. JWT (HS256)

Set `RECLASS_JWT_SECRET`. Tokens must include:

```json
{
  "sub": "user-uuid-or-id",
  "tenant_id": "tenant-uuid",
  "roles": ["reviewer"],
  "exp": 1735689600
}
```

Issue tokens from your identity provider or use the test helper
`api.auth.issue_jwt()` for local tooling. This path is disabled when
`RECLASS_AUTH_MODE=oidc`.

### 3. Static API keys

Set `RECLASS_API_KEYS` to a JSON object:

```json
{
  "my-service-key-abc123": {
    "tenant_id": "550e8400-e29b-41d4-a716-446655440000",
    "user_id": "batch-reanalysis",
    "roles": ["operator"],
    "display_name": "Nightly reanalysis job"
  }
}
```

Send: `Authorization: Bearer my-service-key-abc123`

This path is disabled when `RECLASS_AUTH_MODE=oidc`.

## Roles and permissions

| Role | Capabilities |
|---|---|
| `viewer` | Read classifications, reports, alerts, and the de-identified worklist |
| `reviewer` | Viewer + persist drafts, sign-off, alert state changes, worklist writes/transitions, and PHI-gated case detail |
| `operator` | Operational draft/worklist/PHI/alert access + reanalysis, webhook management/emission, and security audit writes; no clinical sign-off |
| `admin` | All tenant-scoped permissions including dev validation endpoint |

### Platform operator vs. tenant admin

The `admin` role is **tenant-scoped**: it administers only its own tenant. Tokens
self-assert roles, so a tenant `admin` is *not* permitted to administer the
cross-tenant registry (`clinical.tenant`, which holds every tenant's OIDC binding) —
otherwise a tenant could rewrite another tenant's `oidc_issuer` and defeat the tenant
binding above. Cross-tenant registry operations (`POST /admin/tenants`,
`PATCH /admin/tenants/{id}`, listing/reading other tenants) require **platform-operator**
authority:

- In production a principal is a platform operator only if its token `sub` is in the
  server-configured `RECLASS_PLATFORM_ADMINS` allowlist (and, when
  `RECLASS_PLATFORM_OIDC_ISSUER` is set, its issuer matches the platform IdP). The
  role alone is never sufficient.
- In development the relaxed single-operator posture applies (any `admin` session).

A tenant `admin` keeps read-only access to **its own** tenant row and readiness; a
request for another tenant returns 404 (existence is not disclosed).

Permission strings checked at the router layer include
`classification:read`, `classification:write`, `classification:sign_off`,
`alert:read`, `alert:write`, `reanalysis:run`, `audit:read`, `classify:preview`,
`audit:write`, `tenant:admin`, `webhook:admin`, `webhook:emit`,
`evidence:resolve`, `case:read`, `case:read_phi`, `case:write`,
`case:transition`, and `validation:run`.

## Tenant header consistency

When both `Authorization` and `X-Tenant-Id` are sent, the tenant in the token
must match the header. A mismatch returns HTTP 403.

## Audit trail

Sign-off, alert state changes, classification creation, worklist case creation/
updates/transitions/bulk actions/PHI reads, and reanalysis runs are appended to
the operational audit log (`GET /audit`). Structured security
events are recorded as `security.*` actions through `POST /audit/security-events`.
Configure `RECLASS_AUDIT_BACKEND=db` in production and apply the ordered
migrations.

Audit retention is policy-driven: configure `RECLASS_AUDIT_RETENTION_DAYS` and
apply it explicitly with `POST /audit/retention/apply` from an operator/admin
session. Do not prune production audit logs without the lab's retention approval.

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `RECLASS_OIDC_ISSUER` | (empty) | Expected issuer; with JWKS enables RS256 OIDC validation |
| `RECLASS_OIDC_AUDIENCE` | (empty) | Expected JWT audience when set |
| `RECLASS_OIDC_JWKS_URL` | (empty) | Identity-provider JWKS endpoint |
| `RECLASS_OIDC_JWKS` | `{}` | Static/pinned JWKS JSON for tests or air-gapped deploys |
| `RECLASS_AUTH_MODE` | `oidc` in prod, else `auto` | Token fallback mode |
| `RECLASS_PLATFORM_ADMINS` | (empty) | Allowlist of platform-operator token `sub`s (comma- or JSON-list). Empty ⇒ no cross-tenant admin outside development |
| `RECLASS_PLATFORM_OIDC_ISSUER` | (empty) | When set, a platform operator's token issuer must equal this (binds the allowlist to the platform IdP) |
| `RECLASS_JWT_SECRET` | (empty) | HS256 signing secret |
| `RECLASS_API_KEYS` | `{}` | Static API key map (JSON) |
| `RECLASS_LEGACY_ROLES` | `reviewer` | Roles for dev header-only sessions |
| `RECLASS_AUDIT_BACKEND` | `memory` | `memory` or `db` |
| `RECLASS_AUDIT_RETENTION_DAYS` | `365` | Audit retention age for explicit pruning |
| `RECLASS_AUDIT_MAX_ENTRIES` | `10000` | In-memory audit retention cap |

## Secret and JWKS Rotation Runbook

1. Prefer IdP-managed JWKS rotation with stable `RECLASS_OIDC_JWKS_URL`. The
   verifier refetches on TTL expiry and on unknown `kid`, rate-limited to avoid
   hammering the provider.
2. For pinned `RECLASS_OIDC_JWKS`, deploy the new key set before the IdP starts
   signing with the new key. Keep the old key until all previously issued tokens
   expire.
3. Record a `security.jwks_rotation` or `security.secret_rotation` audit event
   with the operator, change ticket, old/new key IDs, and validation outcome.
4. Verify `/health/preflight` after rotation. In production, a missing issuer,
   missing JWKS, or non-`oidc` auth mode fails preflight.
5. For any emergency HS256/API-key use, switch only a non-production environment
   to `RECLASS_AUTH_MODE=auto`. Production should remain `oidc`.

## Frontend session

The reviewer UI at `/reviewer/` stores only the API base URL and tenant UUID in
browser local storage. Its production-safe default keeps the Bearer token in
memory and clears it on reload; a development-only source flag can opt into token
persistence and must not be enabled in production. Production deployments should
integrate with SSO/OIDC and inject short-lived RS256 bearer tokens.
