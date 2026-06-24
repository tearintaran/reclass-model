# Deployment guide

This document describes how to deploy the ReClass API, reviewer frontend, and
PostgreSQL backend.

Status as of 2026-06-19: this is a concrete local/staging deployment scaffold with
fail-closed production preflight, OIDC-only production auth mode, rate/request
limits, tenant administration, audit-retention hooks, webhook delivery, SLO metric
surfaces, and a Docker/PostgreSQL test profile. It is still not a clinical
production deployment without the validation, QMS, hosting, security-review, and
operational controls described in `../../roadmap.md`.

## Prerequisites

- PostgreSQL 16
- Python 3.11+ (or use the provided Docker image)
- Provider caches (REVEL, gnomAD) and optional ClinGen fixture for evidence resolution

## Quick start (Docker Compose)

From `ReClass Model/`:

```bash
docker compose -f deploy/docker-compose.yml up --build
```

This starts PostgreSQL, applies `db/schema.sql` and ordered migrations, and
serves the API at `http://localhost:8000`.

- Health: `GET /health`
- Preflight/readiness: `GET /health/preflight`
- Metrics: `GET /metrics` (Prometheus text format)
- Reviewer UI: `http://localhost:8000/reviewer/`

Run the full test suite against the Compose PostgreSQL service:

```bash
docker compose -f deploy/docker-compose.yml --profile test run --rm test
```

## Manual deployment

### 1. Install dependencies

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt -r api/requirements.txt
```

### 2. Initialize the database

```bash
python db/apply.py reclass_prod
```

`db/apply.py` creates the database if needed, applies `db/schema.sql`, and then
applies every `deploy/migrations/*.sql` file in filename order. Applied
migrations are recorded in `public.reclass_schema_migrations` with filename,
SHA-256 checksum, timestamp, duration, and status. Re-running the command skips
matching migrations and fails if an already-applied migration file has changed.

Create a tenant and application role per your lab policy. Use
`storage/db.py` helpers (`ensure_app_role`, `grant_app_role`) to enforce RLS.

### 3. Configure environment

| Variable | Required (prod) | Description |
|---|---|---|
| `RECLASS_API_ENV` | yes | `production` |
| `RECLASS_DB` | yes | PostgreSQL database name |
| `RECLASS_DB_ROLE` | yes | Non-superuser role for RLS |
| `RECLASS_AUTH_MODE` | yes | `oidc` in production; disables HS256/API-key fallback |
| `RECLASS_OIDC_ISSUER` | yes | Expected identity-provider issuer for RS256/JWKS validation |
| `RECLASS_OIDC_AUDIENCE` | recommended | Expected API audience when issued by the IdP |
| `RECLASS_OIDC_JWKS_URL` or `RECLASS_OIDC_JWKS` | yes | JWKS endpoint or pinned JWKS JSON |
| `RECLASS_JWT_SECRET` | no | HS256 local/dev secret; ignored when `RECLASS_AUTH_MODE=oidc` |
| `RECLASS_API_KEYS` | no | Static local/dev/service keys; ignored when `RECLASS_AUTH_MODE=oidc` |
| `RECLASS_AUDIT_BACKEND` | yes | Set to `db` |
| `RECLASS_AUDIT_RETENTION_DAYS` | recommended | Age threshold for explicit audit pruning |
| `RECLASS_RATE_LIMIT_PER_MINUTE` | recommended | Per-client/path API guard |
| `RECLASS_REQUEST_SIZE_LIMIT_BYTES` | recommended | Request body cap from `Content-Length` |
| `RECLASS_REFERENCE_METADATA` | yes | Reference FASTA metadata sidecar |
| `RECLASS_PROVIDER_CACHE_MANIFEST` | yes | Provider-cache manifest file or directory |
| `RECLASS_PREFLIGHT_ON_STARTUP` | default yes in prod | Run strict preflight at startup |
| `RECLASS_PREFLIGHT_CHECK_DATABASE` | default yes in prod | Check DB role, RLS, and migration ledger |
| `RECLASS_CLINGEN_FIXTURE` | optional | Path to ClinGen fixture |

See [auth.md](auth.md) for authentication configuration.

`deploy/docker-compose.yml` is a local/staging example and contains demo database
passwords and a demo HS256 secret. Replace them with environment/secret injection
before any shared deployment.

### 4. Run the API

```bash
python -m uvicorn api.app:app --host 0.0.0.0 --port 8000
```

For production, run behind a reverse proxy (TLS termination and network policy)
and use a process manager (systemd, Kubernetes, etc.). Keep the in-app rate and
request-size limits enabled even when a proxy also enforces them.

### 5. Serve the reviewer frontend

The API mounts `frontend/` at `/reviewer/` automatically when the directory
exists. Alternatively, serve static files from your CDN and point the UI at the
API base URL.

## Backups

Assumptions for the current deployment scaffold:

- Backups run daily at minimum, with additional pre-upgrade backups before every
  production deploy or migration.
- The default script retention keeps the most recent 14 daily-style backups; set
  a longer retention window in managed backup storage for clinical environments.
- Target RPO for the scaffold is 24 hours unless the deployment adds WAL
  archiving or managed continuous backup.
- Target RTO for the scaffold is 4 hours for a rehearsed single-database restore.
  HA/failover, WAL replay, and cross-region recovery are not implemented here.

Create a backup:

```bash
RECLASS_DB=reclass_prod RECLASS_BACKUP_DIR=/var/backups/reclass ./deploy/backup.sh
```

Restore into an explicitly named fresh database:

```bash
RECLASS_RESTORE_SOURCE=/var/backups/reclass/reclass_prod_20260101T000000Z.sql.gz \
RECLASS_RESTORE_TARGET_DB=reclass_restore_test \
./deploy/restore.sh
```

`deploy/restore.sh` refuses to choose a target by default. If the target database
already exists, it fails unless `RECLASS_RESTORE_DROP=1` is set. Use that flag
only for disposable restore targets, never for a clinical production database.

Quarterly restore rehearsal:

1. Create a current production backup with `deploy/backup.sh`.
2. Restore it to a fresh non-production database with `deploy/restore.sh`.
3. Run `python db/apply.py reclass_restore_test` to confirm all schema
   migrations are present and checksum-compatible.
4. Verify row counts for `clinical.tenant`, `clinical.patient`,
   `clinical.classification`, `research.evidence_bundle`, `clinical.alert`, and
   `clinical.audit_log`.
5. Verify RLS with a non-owner application role: tenant A must not read tenant B
   patients, classifications, alerts, or audit rows.
6. Reconstruct at least one restored classification receipt with
   `storage.verify.verify_classification`.
7. Record elapsed restore time, backup timestamp, observed RTO/RPO, operator,
   database version, application commit, and any remediation.

For a local executable rehearsal when PostgreSQL is available:

```bash
python -m unittest tests.test_db_migrations -v
```

Backups include all clinical tables (classifications, alerts, reanalysis queue,
audit log). Research-schema data is de-identified and may be backed up separately.

Restore testing is required before clinical production use. The scripts are
concrete building blocks, not proof that disaster recovery is production-ready in
the target hosting environment.

## Migration Ledger

Inspect the applied migration ledger:

```bash
psql reclass_prod -c \
  "SELECT migration_id, filename, checksum_sha256, applied_at, duration_ms, status
     FROM public.reclass_schema_migrations
    ORDER BY migration_id"
```

Verify local migration file checksums against the database:

```bash
python db/apply.py reclass_prod
```

The command is safe to re-run: matching checksums are skipped. A checksum
mismatch means an already-applied migration file changed after it was recorded.
Do not edit the ledger to bypass this. Recovery path:

1. Stop the deployment before applying further migrations.
2. Compare the deployed migration file with the version recorded at the commit
   that originally applied it.
3. Restore the historical file content if the repository drifted.
4. If the database schema itself drifted, restore from backup or write a new
   forward-only corrective migration.
5. Re-run `python db/apply.py reclass_prod` and archive the incident notes under
   the deployment/change-control record.

Production startup preflight verifies that the latest local migration is present
in `public.reclass_schema_migrations` when `RECLASS_PREFLIGHT_CHECK_DATABASE` is
enabled. A missing or checksum-mismatched migration blocks startup.

## Observability

- **Structured logs**: JSON request lines on stdout (`reclass.api` logger)
- **Health endpoint**: `/health` returns request count and readiness check names
- **Readiness endpoint**: `/health/preflight` reports reference metadata,
  provider-cache manifests, OIDC/JWKS, audit backend, DB role/RLS, migration
  ledger, and restore-test metadata
- **Metrics endpoint**: `/metrics` exposes request/error counters, average
  latency, failed evidence-resolution counter, security-event counter, provider
  cache age, reanalysis lag, alert backlog, and restore-test age

Integrate with your log aggregator and Prometheus/Grafana stack.

## Tenant Admin, Onboarding, and Webhooks

Tenant administration lives under `/admin/tenants` and requires the `admin` role.
Use `/admin/tenants/{tenant_id}/readiness` before go-live to check source-cache
setup, reference-cache metadata, OIDC setup, a sample classification smoke test,
and the platform preflight report.

Webhook endpoints live under `/webhooks/endpoints` and support signed outbound
events for `tier_crossing`, `source_snapshot_update`, `config_change`, and
`reanalysis_completed`. Delivery jobs are HMAC-SHA256 signed with
`X-ReClass-Signature` and retried with exponential backoff by the worker using
`api.webhooks.deliver_due`.

## Container image

Build from `ReClass Model/`:

```bash
docker build -f deploy/Dockerfile -t reclass-api:latest .
```

The image sets `RECLASS_API_ENV=production` and `RECLASS_AUDIT_BACKEND=db` by
default. Override via environment at runtime.

## Upgrade checklist

1. Run the full unit test suite
2. Create a pre-upgrade backup
3. Apply schema and ordered migrations with `python db/apply.py reclass_prod`
4. Verify the migration ledger checksums and `/health/preflight`
5. Deploy API with rolling restart
6. Verify `/health`, `/metrics`, and a smoke-test classification workflow
7. Review [release_review.md](release_review.md) reports if engine version changed
