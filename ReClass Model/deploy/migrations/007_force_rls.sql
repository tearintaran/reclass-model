-- FORCE ROW LEVEL SECURITY on every tenant-scoped table.
--
-- A plain `ENABLE ROW LEVEL SECURITY` (schema.sql + migrations 001-006) is bypassed
-- by the table owner and by superusers, so tenant isolation held only as long as the
-- role used inside `tenant_session` happened to be a non-owner. `FORCE` removes the
-- owner bypass so the policies apply even to the table owner; a misconfigured tenant
-- role can no longer silently read across tenants. Superuser / BYPASSRLS roles still
-- bypass by design -- that is the controlled path for cross-tenant background workers
-- (e.g. webhook delivery), whose connection must therefore be a BYPASSRLS/superuser
-- role while the per-request `RECLASS_DB_ROLE` is a non-superuser, non-BYPASSRLS role.
--
-- `FORCE ROW LEVEL SECURITY` is idempotent, so re-applying (or applying over the new
-- inline FORCE in schema.sql) is a no-op. This migration also retrofits deployments
-- whose 001-006 migrations were applied before FORCE existed.

ALTER TABLE clinical.patient                FORCE ROW LEVEL SECURITY;
ALTER TABLE clinical.classification         FORCE ROW LEVEL SECURITY;
ALTER TABLE clinical.alert                  FORCE ROW LEVEL SECURITY;
ALTER TABLE clinical.reanalysis_event       FORCE ROW LEVEL SECURITY;
ALTER TABLE clinical.reanalysis_queue       FORCE ROW LEVEL SECURITY;
ALTER TABLE clinical.reanalysis_run         FORCE ROW LEVEL SECURITY;
ALTER TABLE clinical.audit_log              FORCE ROW LEVEL SECURITY;
ALTER TABLE clinical.evidence_coverage      FORCE ROW LEVEL SECURITY;
ALTER TABLE clinical.curation_queue         FORCE ROW LEVEL SECURITY;
ALTER TABLE clinical.reanalysis_policy      FORCE ROW LEVEL SECURITY;
ALTER TABLE clinical.amended_report         FORCE ROW LEVEL SECURITY;
ALTER TABLE clinical.clinician_notification FORCE ROW LEVEL SECURITY;
ALTER TABLE clinical.webhook_endpoint       FORCE ROW LEVEL SECURITY;
ALTER TABLE clinical.webhook_event          FORCE ROW LEVEL SECURITY;
ALTER TABLE clinical.webhook_delivery       FORCE ROW LEVEL SECURITY;
ALTER TABLE clinical.worklist_case          FORCE ROW LEVEL SECURITY;
