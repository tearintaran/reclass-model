-- Operational audit log for sign-off, alert state changes, and reanalysis actions.
-- Applied by db/apply.py after db/schema.sql. Keep migration files free of
-- explicit BEGIN/COMMIT so the apply tool can wrap SQL and ledger writes in one
-- transaction.

CREATE TABLE IF NOT EXISTS clinical.audit_log (
    audit_id        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       uuid NOT NULL REFERENCES clinical.tenant(tenant_id),
    actor_id        text NOT NULL,
    action          text NOT NULL,
    resource_type   text NOT NULL,
    resource_id     text NOT NULL,
    detail          jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_audit_tenant_created
    ON clinical.audit_log (tenant_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_audit_action
    ON clinical.audit_log (tenant_id, action);

ALTER TABLE clinical.audit_log ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS tenant_isolation_audit_log ON clinical.audit_log;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'tenant_isolation_audit_log') THEN
        CREATE POLICY tenant_isolation_audit_log ON clinical.audit_log
            USING (tenant_id = current_setting('app.current_tenant', true)::uuid);
    END IF;
END$$;
