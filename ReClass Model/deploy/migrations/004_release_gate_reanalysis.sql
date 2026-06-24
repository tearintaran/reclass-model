-- Release-gate enforcement, reanalysis policies, alert triage, and amended-report
-- tracking for Job 2. Applied by db/apply.py after db/schema.sql; keep this file
-- free of explicit BEGIN/COMMIT so the apply tool can wrap it with the ledger write.

ALTER TABLE clinical.classification
    ADD COLUMN IF NOT EXISTS release_state text NOT NULL DEFAULT 'review_pending',
    ADD COLUMN IF NOT EXISTS signoff_packet jsonb NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS release_scope jsonb NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS config_hash text,
    ADD COLUMN IF NOT EXISTS source_snapshots jsonb NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS validation_report_id text,
    ADD COLUMN IF NOT EXISTS conflict_policy_disposition text,
    ADD COLUMN IF NOT EXISTS reviewer_credential text,
    ADD COLUMN IF NOT EXISTS institutional_authorization text,
    ADD COLUMN IF NOT EXISTS effective_date date,
    ADD COLUMN IF NOT EXISTS re_review_date date,
    ADD COLUMN IF NOT EXISTS assigned_reviewer text,
    ADD COLUMN IF NOT EXISTS second_reviewer text,
    ADD COLUMN IF NOT EXISTS second_review_at timestamptz,
    ADD COLUMN IF NOT EXISTS override_rationale text,
    ADD COLUMN IF NOT EXISTS release_notes text,
    ADD COLUMN IF NOT EXISTS approved_at timestamptz,
    ADD COLUMN IF NOT EXISTS released_at timestamptz,
    ADD COLUMN IF NOT EXISTS withdrawn_at timestamptz,
    ADD COLUMN IF NOT EXISTS rereview_required_at timestamptz;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'classification_release_state_check'
    ) THEN
        ALTER TABLE clinical.classification
            ADD CONSTRAINT classification_release_state_check
            CHECK (release_state IN (
                'review_pending',
                'approved_for_release',
                'released',
                'withdrawn',
                're-review_required'
            ));
    END IF;
END$$;

CREATE INDEX IF NOT EXISTS idx_classification_release_state
    ON clinical.classification (tenant_id, release_state);

ALTER TABLE clinical.alert
    ADD COLUMN IF NOT EXISTS triage_owner text,
    ADD COLUMN IF NOT EXISTS sla_due_at timestamptz,
    ADD COLUMN IF NOT EXISTS severity text NOT NULL DEFAULT 'standard',
    ADD COLUMN IF NOT EXISTS resolution_rationale text,
    ADD COLUMN IF NOT EXISTS re_review_outcome text,
    ADD COLUMN IF NOT EXISTS notification_state text NOT NULL DEFAULT 'not_required',
    ADD COLUMN IF NOT EXISTS triaged_at timestamptz;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'alert_severity_check'
    ) THEN
        ALTER TABLE clinical.alert
            ADD CONSTRAINT alert_severity_check
            CHECK (severity IN ('low', 'standard', 'high', 'critical'));
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'alert_notification_state_check'
    ) THEN
        ALTER TABLE clinical.alert
            ADD CONSTRAINT alert_notification_state_check
            CHECK (notification_state IN (
                'not_required', 'pending', 'sent', 'acknowledged', 'failed'
            ));
    END IF;
END$$;

CREATE INDEX IF NOT EXISTS idx_alert_triage
    ON clinical.alert (tenant_id, severity, notification_state, sla_due_at);

CREATE TABLE IF NOT EXISTS clinical.reanalysis_policy (
    policy_id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id              uuid NOT NULL REFERENCES clinical.tenant(tenant_id),
    cadence                text NOT NULL,
    included_sources       jsonb NOT NULL DEFAULT '[]'::jsonb,
    affected_scope         jsonb NOT NULL DEFAULT '{}'::jsonb,
    escalation_thresholds  jsonb NOT NULL DEFAULT '{}'::jsonb,
    retention              jsonb NOT NULL DEFAULT '{}'::jsonb,
    enabled                boolean NOT NULL DEFAULT true,
    updated_at             timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id)
);

ALTER TABLE clinical.reanalysis_policy ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'tenant_isolation_reanalysis_policy') THEN
        CREATE POLICY tenant_isolation_reanalysis_policy ON clinical.reanalysis_policy
            USING (tenant_id = current_setting('app.current_tenant', true)::uuid);
    END IF;
END$$;

CREATE TABLE IF NOT EXISTS clinical.amended_report (
    amended_report_id       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id               uuid NOT NULL REFERENCES clinical.tenant(tenant_id),
    classification_id       uuid NOT NULL REFERENCES clinical.classification(classification_id),
    report_id               text NOT NULL,
    previous_report_id      text,
    state                   text NOT NULL,
    amendment_reason        text,
    payload_sha256          text NOT NULL,
    payload                 jsonb NOT NULL,
    notification_state      text NOT NULL DEFAULT 'pending',
    created_at              timestamptz NOT NULL DEFAULT now()
);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'amended_report_state_check'
    ) THEN
        ALTER TABLE clinical.amended_report
            ADD CONSTRAINT amended_report_state_check
            CHECK (state IN ('draft', 'final', 'amended'));
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'amended_report_notification_state_check'
    ) THEN
        ALTER TABLE clinical.amended_report
            ADD CONSTRAINT amended_report_notification_state_check
            CHECK (notification_state IN ('not_required', 'pending', 'sent', 'acknowledged', 'failed'));
    END IF;
END$$;

CREATE INDEX IF NOT EXISTS idx_amended_report_classification
    ON clinical.amended_report (tenant_id, classification_id, created_at DESC);

ALTER TABLE clinical.amended_report ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'tenant_isolation_amended_report') THEN
        CREATE POLICY tenant_isolation_amended_report ON clinical.amended_report
            USING (tenant_id = current_setting('app.current_tenant', true)::uuid);
    END IF;
END$$;

CREATE TABLE IF NOT EXISTS clinical.clinician_notification (
    notification_id     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           uuid NOT NULL REFERENCES clinical.tenant(tenant_id),
    amended_report_id   uuid REFERENCES clinical.amended_report(amended_report_id),
    classification_id   uuid REFERENCES clinical.classification(classification_id),
    recipient           text NOT NULL,
    channel             text NOT NULL DEFAULT 'ehr',
    notification_state  text NOT NULL DEFAULT 'pending',
    sent_at             timestamptz,
    acknowledged_at     timestamptz,
    rationale           text,
    created_at          timestamptz NOT NULL DEFAULT now()
);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'clinician_notification_state_check'
    ) THEN
        ALTER TABLE clinical.clinician_notification
            ADD CONSTRAINT clinician_notification_state_check
            CHECK (notification_state IN ('not_required', 'pending', 'sent', 'acknowledged', 'failed'));
    END IF;
END$$;

CREATE INDEX IF NOT EXISTS idx_clinician_notification_state
    ON clinical.clinician_notification (tenant_id, notification_state, created_at DESC);

ALTER TABLE clinical.clinician_notification ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies WHERE policyname = 'tenant_isolation_clinician_notification'
    ) THEN
        CREATE POLICY tenant_isolation_clinician_notification ON clinical.clinician_notification
            USING (tenant_id = current_setting('app.current_tenant', true)::uuid);
    END IF;
END$$;
