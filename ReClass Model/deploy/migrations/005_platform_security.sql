-- Platform security, tenant administration, and webhook delivery (job3).
-- Applied by db/apply.py after db/schema.sql; keep this file free of explicit
-- BEGIN/COMMIT so the apply tool can wrap it with the ledger write.

ALTER TABLE clinical.tenant
    ADD COLUMN IF NOT EXISTS slug text,
    ADD COLUMN IF NOT EXISTS status text NOT NULL DEFAULT 'active',
    ADD COLUMN IF NOT EXISTS contact_email text,
    ADD COLUMN IF NOT EXISTS oidc_issuer text,
    ADD COLUMN IF NOT EXISTS oidc_audience text,
    ADD COLUMN IF NOT EXISTS updated_at timestamptz NOT NULL DEFAULT now();

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'tenant_status_check'
    ) THEN
        ALTER TABLE clinical.tenant
            ADD CONSTRAINT tenant_status_check
            CHECK (status IN ('onboarding', 'active', 'suspended', 'decommissioned'));
    END IF;
END$$;

CREATE UNIQUE INDEX IF NOT EXISTS uq_tenant_slug
    ON clinical.tenant (slug)
    WHERE slug IS NOT NULL;

CREATE TABLE IF NOT EXISTS clinical.webhook_endpoint (
    endpoint_id   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id     uuid NOT NULL REFERENCES clinical.tenant(tenant_id),
    url           text NOT NULL,
    secret        text NOT NULL,
    event_types   jsonb NOT NULL DEFAULT '[]'::jsonb,
    description   text NOT NULL DEFAULT '',
    enabled       boolean NOT NULL DEFAULT true,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_webhook_endpoint_tenant
    ON clinical.webhook_endpoint (tenant_id, enabled);

CREATE TABLE IF NOT EXISTS clinical.webhook_event (
    event_id      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id     uuid NOT NULL REFERENCES clinical.tenant(tenant_id),
    event_type    text NOT NULL,
    source_id     text,
    payload       jsonb NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_webhook_event_tenant_created
    ON clinical.webhook_event (tenant_id, created_at DESC);

CREATE TABLE IF NOT EXISTS clinical.webhook_delivery (
    delivery_id        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id          uuid NOT NULL REFERENCES clinical.tenant(tenant_id),
    event_id           uuid NOT NULL REFERENCES clinical.webhook_event(event_id),
    endpoint_id        uuid NOT NULL REFERENCES clinical.webhook_endpoint(endpoint_id),
    event_type         text NOT NULL,
    url                text NOT NULL,
    payload            jsonb NOT NULL,
    state              text NOT NULL DEFAULT 'pending',
    attempts           integer NOT NULL DEFAULT 0,
    last_status_code   integer,
    last_response_body text,
    next_attempt_at    timestamptz NOT NULL DEFAULT now(),
    created_at         timestamptz NOT NULL DEFAULT now(),
    delivered_at       timestamptz,
    CHECK (state IN ('pending', 'retry', 'delivered', 'failed'))
);

CREATE INDEX IF NOT EXISTS idx_webhook_delivery_due
    ON clinical.webhook_delivery (state, next_attempt_at, created_at);
CREATE INDEX IF NOT EXISTS idx_webhook_delivery_tenant_state
    ON clinical.webhook_delivery (tenant_id, state, created_at DESC);

ALTER TABLE clinical.webhook_endpoint ENABLE ROW LEVEL SECURITY;
ALTER TABLE clinical.webhook_event    ENABLE ROW LEVEL SECURITY;
ALTER TABLE clinical.webhook_delivery ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'tenant_isolation_webhook_endpoint') THEN
        CREATE POLICY tenant_isolation_webhook_endpoint ON clinical.webhook_endpoint
            USING (tenant_id = current_setting('app.current_tenant', true)::uuid);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'tenant_isolation_webhook_event') THEN
        CREATE POLICY tenant_isolation_webhook_event ON clinical.webhook_event
            USING (tenant_id = current_setting('app.current_tenant', true)::uuid);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'tenant_isolation_webhook_delivery') THEN
        CREATE POLICY tenant_isolation_webhook_delivery ON clinical.webhook_delivery
            USING (tenant_id = current_setting('app.current_tenant', true)::uuid);
    END IF;
END$$;
