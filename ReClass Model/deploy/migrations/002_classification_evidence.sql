-- Carry the resolved evidence bundle onto the clinical receipt so reviewer and
-- FHIR reports can surface the MANE Select transcript identity (job1 task 4) and
-- PS4 cohort counts (job1 task 5) that were resolved at classification time.
-- Nullable and additive: existing receipts (scored from direct events/signals, or
-- written before this column existed) keep NULL and render exactly as before.
-- Applied by db/apply.py after db/schema.sql. Keep migration files free of
-- explicit BEGIN/COMMIT so the apply tool can wrap SQL and ledger writes in one
-- transaction.

ALTER TABLE clinical.classification
    ADD COLUMN IF NOT EXISTS evidence jsonb;
