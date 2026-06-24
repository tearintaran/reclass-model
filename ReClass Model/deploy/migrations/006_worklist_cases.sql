-- Variant case worklist (product layer): the case/order model that sits above
-- the de-identified classification receipts and drives the daily reviewer queue.
-- Applied by db/apply.py after db/schema.sql; keep this file free of explicit
-- BEGIN/COMMIT so the apply tool can wrap it with the ledger write.

CREATE TABLE IF NOT EXISTS clinical.worklist_case (
    case_id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id          uuid NOT NULL REFERENCES clinical.tenant(tenant_id),
    accession          text NOT NULL,
    status             text NOT NULL DEFAULT 'draft',
    priority           text NOT NULL DEFAULT 'routine',
    assigned_to        text,
    -- Operational (non-PHI) order context.
    specimen_id        text,
    specimen_type      text,
    ordering_provider  text,
    ordering_facility  text,
    test_code          text,
    -- PHI context: access-controlled, redacted from de-identified views by the
    -- service layer (see worklist.case.PHI_FIELDS / case:read_phi).
    patient_mrn        text,
    patient_name       text,
    indication         text,
    -- Turnaround clock.
    received_at        timestamptz,
    due_at             timestamptz,
    notes              text,
    -- Links to the de-identified clinical.classification receipts on this order.
    classification_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
    signed_at          timestamptz,
    released_at        timestamptz,
    history            jsonb NOT NULL DEFAULT '[]'::jsonb,
    created_at         timestamptz NOT NULL DEFAULT now(),
    updated_at         timestamptz NOT NULL DEFAULT now(),
    CHECK (status IN ('draft', 'in_review', 'signed', 'released', 'on_hold', 'cancelled')),
    CHECK (priority IN ('stat', 'urgent', 'routine'))
);

-- The accession is the lab's order id; it is unique within a tenant.
CREATE UNIQUE INDEX IF NOT EXISTS uq_worklist_case_accession
    ON clinical.worklist_case (tenant_id, accession);

-- Worklist surfaces: by status/assignee, and the due-date / unassigned views.
CREATE INDEX IF NOT EXISTS idx_worklist_case_tenant_status
    ON clinical.worklist_case (tenant_id, status, priority);
CREATE INDEX IF NOT EXISTS idx_worklist_case_tenant_due
    ON clinical.worklist_case (tenant_id, due_at);
CREATE INDEX IF NOT EXISTS idx_worklist_case_tenant_assignee
    ON clinical.worklist_case (tenant_id, assigned_to);

ALTER TABLE clinical.worklist_case ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies WHERE policyname = 'tenant_isolation_worklist_case'
    ) THEN
        CREATE POLICY tenant_isolation_worklist_case ON clinical.worklist_case
            USING (tenant_id = current_setting('app.current_tenant', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid);
    END IF;
END$$;
