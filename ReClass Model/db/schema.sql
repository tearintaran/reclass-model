-- Standardized Variant Reclassification Engine — system of record (spec 02 / 11)
-- PostgreSQL 16.
--
-- Two strictly separated data domains:
--   * clinical  — identified, per-tenant, protected by ROW-LEVEL SECURITY (RLS).
--   * research  — de-identified, NO identifiers and NO join path back to a patient.
--
-- The boundary is structural: research tables carry no patient/tenant keys, so a
-- query in the research schema cannot reach identified data even by mistake.
--
-- Apply with:  psql <db> -f db/schema.sql

BEGIN;

CREATE SCHEMA IF NOT EXISTS clinical;
CREATE SCHEMA IF NOT EXISTS research;

-- --------------------------------------------------------------------------- --
-- Shared enumerations                                                         --
-- --------------------------------------------------------------------------- --
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'acmg_tier') THEN
        CREATE TYPE acmg_tier AS ENUM (
            'Benign', 'Likely Benign', 'VUS', 'Likely Pathogenic', 'Pathogenic'
        );
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'alert_state') THEN
        CREATE TYPE alert_state AS ENUM (
            'open', 'acknowledged', 'in_review', 'resolved', 'dismissed'
        );
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'evidence_direction') THEN
        CREATE TYPE evidence_direction AS ENUM ('pathogenic', 'benign', 'neutral');
    END IF;
END$$;

-- --------------------------------------------------------------------------- --
-- CLINICAL (identified, tenant-isolated via RLS)                              --
-- --------------------------------------------------------------------------- --
CREATE TABLE IF NOT EXISTS clinical.tenant (
    tenant_id     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name          text NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS clinical.patient (
    patient_id    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id     uuid NOT NULL REFERENCES clinical.tenant(tenant_id),
    mrn           text NOT NULL,                 -- identified
    created_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, mrn)
);

CREATE TABLE IF NOT EXISTS clinical.variant (
    variant_id    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    chrom         text NOT NULL,
    pos           bigint NOT NULL,
    ref           text NOT NULL,
    alt           text NOT NULL,
    build         text NOT NULL DEFAULT 'GRCh38',
    UNIQUE (build, chrom, pos, ref, alt)
);

CREATE TABLE IF NOT EXISTS clinical.classification (
    classification_id   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           uuid NOT NULL REFERENCES clinical.tenant(tenant_id),
    patient_id          uuid REFERENCES clinical.patient(patient_id),
    variant_id          uuid NOT NULL REFERENCES clinical.variant(variant_id),
    tier                acmg_tier NOT NULL,
    total_points        numeric NOT NULL,
    engine_version      text NOT NULL,
    reconstruction_hash text NOT NULL,          -- SHA-256 over (evidence, engine_version)
    contributions       jsonb NOT NULL,         -- full per-criterion breakdown (auditable)
    overrides           jsonb NOT NULL DEFAULT '[]'::jsonb,
    signed_off_by       text,                   -- credentialed human; NULL until sign-off
    signed_off_at       timestamptz,
    created_at          timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_classification_variant ON clinical.classification (variant_id);
CREATE INDEX IF NOT EXISTS idx_classification_tenant ON clinical.classification (tenant_id);

-- Continuous-reanalysis alerts; only TIER CROSSINGS create rows (spec 06).
CREATE TABLE IF NOT EXISTS clinical.alert (
    alert_id        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       uuid NOT NULL REFERENCES clinical.tenant(tenant_id),
    variant_id      uuid NOT NULL REFERENCES clinical.variant(variant_id),
    old_tier        acmg_tier NOT NULL,
    new_tier        acmg_tier NOT NULL,
    serious         boolean NOT NULL,
    state           alert_state NOT NULL DEFAULT 'open',
    created_at      timestamptz NOT NULL DEFAULT now(),
    resolved_at     timestamptz,
    CHECK (old_tier <> new_tier)               -- a non-crossing must never create an alert
);
CREATE INDEX IF NOT EXISTS idx_alert_state ON clinical.alert (tenant_id, state);

-- Continuous-reanalysis audit log (spec 06 / gap §8). EVERY reanalysis outcome is
-- recorded here -- including same-tier point changes that intentionally page no one
-- (``crossed = false``, ``alert_id`` NULL). Tier crossings additionally create a
-- ``clinical.alert`` row and link it via ``alert_id``. This table is the auditable
-- trail behind "same-tier changes are auditable but do not create high-priority
-- alerts": it links the prior and new classification receipts so the full evidence
-- delta is reconstructable. Tenant-scoped + RLS-protected like the other clinical
-- tables (it carries patient-adjacent reclassification history).
CREATE TABLE IF NOT EXISTS clinical.reanalysis_event (
    reanalysis_id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id               uuid NOT NULL REFERENCES clinical.tenant(tenant_id),
    variant_id              uuid NOT NULL REFERENCES clinical.variant(variant_id),
    prior_classification_id uuid REFERENCES clinical.classification(classification_id),
    new_classification_id   uuid NOT NULL REFERENCES clinical.classification(classification_id),
    old_tier                acmg_tier NOT NULL,
    new_tier                acmg_tier NOT NULL,
    old_points              numeric NOT NULL,
    new_points              numeric NOT NULL,
    trigger                 text NOT NULL,    -- evidence | provider_version | config_version
    crossed                 boolean NOT NULL, -- old_tier <> new_tier
    alert_id                uuid REFERENCES clinical.alert(alert_id),  -- set iff crossed
    created_at              timestamptz NOT NULL DEFAULT now(),
    CHECK (crossed = (old_tier <> new_tier))  -- crossed flag must match the tiers
);
CREATE INDEX IF NOT EXISTS idx_reanalysis_variant ON clinical.reanalysis_event (tenant_id, variant_id);

-- Old/new evidence-bundle receipts captured alongside a reanalysis (gap §5 task 3).
-- These reference the de-identified ``research.evidence_bundle`` rows that produced
-- the prior and new classifications, so the full evidence delta behind a tier change
-- is reconstructable. They are plain uuids (no cross-schema FK): the clinical schema
-- may point at a research bundle via the public link, but the research schema must
-- never reference clinical (that boundary is asserted in tests). They are additive +
-- nullable -- a reanalysis whose receipts predate bundle persistence keeps them NULL.
ALTER TABLE clinical.reanalysis_event
    ADD COLUMN IF NOT EXISTS prior_bundle_id uuid;
ALTER TABLE clinical.reanalysis_event
    ADD COLUMN IF NOT EXISTS new_bundle_id uuid;

-- --------------------------------------------------------------------------- --
-- OPERATIONS: reanalysis work queue + run reports (gap §5)                     --
-- --------------------------------------------------------------------------- --
-- A queue of (tenant, variant) work items that a provider-version / evidence /
-- config-version change has marked as needing reanalysis. Tenant-scoped + RLS-
-- protected like the rest of clinical: a reanalysis touches a tenant's patient
-- classifications, so the work item is tenant-owned. ``state`` drives the operational
-- loop in ``ops/scheduler.py``; ``attempts`` + ``last_error`` back the retry/error
-- handling for missing caches, unavailable references, and invalid identities.
CREATE TABLE IF NOT EXISTS clinical.reanalysis_queue (
    queue_id      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id     uuid NOT NULL REFERENCES clinical.tenant(tenant_id),
    variant_id    uuid NOT NULL REFERENCES clinical.variant(variant_id),
    trigger       text NOT NULL,                 -- evidence | provider_version | config_version
    reason        text,                          -- e.g. 'gnomAD 4.0 -> 4.1'
    state         text NOT NULL DEFAULT 'pending', -- pending|running|done|failed|skipped
    priority      integer NOT NULL DEFAULT 0,    -- higher runs first
    attempts      integer NOT NULL DEFAULT 0,
    last_error    text,
    last_reason_code text,                        -- deterministic failure/skip reason code
    run_id        uuid,                           -- set when a run processes the item
    enqueued_at   timestamptz NOT NULL DEFAULT now(),
    started_at    timestamptz,
    finished_at   timestamptz,
    CHECK (state IN ('pending', 'running', 'done', 'failed', 'skipped'))
);
CREATE INDEX IF NOT EXISTS idx_queue_state ON clinical.reanalysis_queue (tenant_id, state, priority DESC);
-- At most one OUTSTANDING (pending) item per (tenant, variant, trigger): re-enqueuing
-- the same pending work is a no-op, so a noisy trigger cannot flood the queue.
CREATE UNIQUE INDEX IF NOT EXISTS uq_queue_pending
    ON clinical.reanalysis_queue (tenant_id, variant_id, trigger)
    WHERE state = 'pending';

-- One operational reanalysis run: the per-run roll-up the operator reads to see how
-- many variants were checked, unchanged, same-tier changed, tier-crossing, failed,
-- and skipped (gap §5 task 4). ``detail`` holds the deterministic per-variant
-- outcomes + failure reasons so a run is auditable.
CREATE TABLE IF NOT EXISTS clinical.reanalysis_run (
    run_id      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   uuid NOT NULL REFERENCES clinical.tenant(tenant_id),
    trigger     text NOT NULL,
    checked     integer NOT NULL DEFAULT 0,
    unchanged   integer NOT NULL DEFAULT 0,
    same_tier   integer NOT NULL DEFAULT 0,
    crossed     integer NOT NULL DEFAULT 0,
    failed      integer NOT NULL DEFAULT 0,
    skipped     integer NOT NULL DEFAULT 0,
    detail      jsonb NOT NULL DEFAULT '[]'::jsonb,
    started_at  timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    CHECK (checked = unchanged + same_tier + crossed + failed + skipped)
);
CREATE INDEX IF NOT EXISTS idx_run_tenant ON clinical.reanalysis_run (tenant_id, started_at);

-- Row-level security: a session sees only its own tenant's rows.
ALTER TABLE clinical.patient          ENABLE ROW LEVEL SECURITY;
ALTER TABLE clinical.classification   ENABLE ROW LEVEL SECURITY;
ALTER TABLE clinical.alert            ENABLE ROW LEVEL SECURITY;
ALTER TABLE clinical.reanalysis_event ENABLE ROW LEVEL SECURITY;
ALTER TABLE clinical.reanalysis_queue ENABLE ROW LEVEL SECURITY;
ALTER TABLE clinical.reanalysis_run   ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'tenant_isolation_patient') THEN
        CREATE POLICY tenant_isolation_patient ON clinical.patient
            USING (tenant_id = current_setting('app.current_tenant', true)::uuid);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'tenant_isolation_classification') THEN
        CREATE POLICY tenant_isolation_classification ON clinical.classification
            USING (tenant_id = current_setting('app.current_tenant', true)::uuid);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'tenant_isolation_alert') THEN
        CREATE POLICY tenant_isolation_alert ON clinical.alert
            USING (tenant_id = current_setting('app.current_tenant', true)::uuid);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'tenant_isolation_reanalysis') THEN
        CREATE POLICY tenant_isolation_reanalysis ON clinical.reanalysis_event
            USING (tenant_id = current_setting('app.current_tenant', true)::uuid);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'tenant_isolation_queue') THEN
        CREATE POLICY tenant_isolation_queue ON clinical.reanalysis_queue
            USING (tenant_id = current_setting('app.current_tenant', true)::uuid);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'tenant_isolation_run') THEN
        CREATE POLICY tenant_isolation_run ON clinical.reanalysis_run
            USING (tenant_id = current_setting('app.current_tenant', true)::uuid);
    END IF;
END$$;

-- --------------------------------------------------------------------------- --
-- RESEARCH (de-identified; NO identifiers, NO join back to a patient)         --
-- --------------------------------------------------------------------------- --
CREATE TABLE IF NOT EXISTS research.variant (
    variant_key   text PRIMARY KEY,            -- e.g. 'GRCh38-1-100-A-G'; not a clinical FK
    chrom         text NOT NULL,
    pos           bigint NOT NULL,
    ref           text NOT NULL,
    alt           text NOT NULL
);

-- One standardized evidence observation mapped to an ACMG criterion.
CREATE TABLE IF NOT EXISTS research.evidence_events (
    evidence_id     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    variant_key     text NOT NULL REFERENCES research.variant(variant_key),
    source          text NOT NULL,             -- clinvar | gnomad | revel | cohort | ...
    acmg_criterion  text NOT NULL,
    direction       evidence_direction NOT NULL,
    applied_strength text,
    points          numeric,
    source_version  text,
    observed_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_evidence_variant ON research.evidence_events (variant_key);

-- De-identified cohort counts for PS4 (carry no patient identifiers).
CREATE TABLE IF NOT EXISTS research.cohort_counts (
    variant_key     text NOT NULL REFERENCES research.variant(variant_key),
    ancestry        text NOT NULL,
    case_count      integer NOT NULL DEFAULT 0,
    control_count   integer NOT NULL DEFAULT 0,
    PRIMARY KEY (variant_key, ancestry)
);

-- --------------------------------------------------------------------------- --
-- RESEARCH: persisted EvidenceBundle provenance (gap §3)                      --
-- --------------------------------------------------------------------------- --
-- The bundle is what a provider returns (evidence/model.py): the scored events
-- plus the provenance a re-analysis or human reviewer needs to audit where each
-- point came from. We persist it in the RESEARCH schema so a stored classification
-- is fully reconstructable from named evidence + provenance -- and, like every
-- research table, it carries NO patient/tenant identifier and NO join back to the
-- clinical schema. The only cross-domain link remains the public ``variant_key``.
--
-- RETENTION POLICY (gap §3 task 4):
--   * Compact provenance is the SYSTEM OF RECORD and is retained for the life of
--     the variant record: ``provider_versions``, ``warnings``, ``match``,
--     ``source_record.payload_ref``, and the scored ``evidence_events`` rows. None
--     of these are large, and all are required to audit/reconstruct a tier.
--   * Raw provider payloads (``source_record.payload``) are a CONVENIENCE CACHE
--     only. They are never needed to reconstruct a tier (the engine reconstructs
--     from ``evidence_events``), so they MAY be pruned (set to NULL) after a
--     retention window to bound storage. Pruning is non-destructive to audit:
--     ``payload_ref`` + ``provider_versions`` identify how to re-fetch the raw
--     record from its source.
CREATE TABLE IF NOT EXISTS research.evidence_bundle (
    bundle_id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    variant_key       text NOT NULL REFERENCES research.variant(variant_key),
    schema_version    text NOT NULL,                       -- evidence.model SCHEMA_VERSION
    bundle_hash       text NOT NULL,                        -- engine reconstruction_hash over the bundle's events
    provider_versions jsonb NOT NULL DEFAULT '{}'::jsonb,   -- {source: version}
    warnings          jsonb NOT NULL DEFAULT '[]'::jsonb,   -- deterministic data-quality flags
    match             jsonb,                                -- identity-resolution detail (how the bundle joined a source)
    created_at        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_bundle_variant ON research.evidence_bundle (variant_key);
CREATE INDEX IF NOT EXISTS idx_bundle_hash ON research.evidence_bundle (bundle_hash);

-- One raw matched record a bundle's events were derived from. ``payload_ref`` is a
-- compact, retained reference (accession / URL / content hash); ``payload`` is the
-- optional raw payload subject to the retention pruning described above.
CREATE TABLE IF NOT EXISTS research.source_record (
    source_record_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    bundle_id        uuid NOT NULL REFERENCES research.evidence_bundle(bundle_id) ON DELETE CASCADE,
    ordinal          integer NOT NULL,            -- stable position within the bundle
    source           text,                        -- clinvar | gnomad | revel | cohort | ...
    payload_ref      text,                        -- compact retained reference (never pruned)
    payload          jsonb,                       -- raw provider payload (prunable cache)
    UNIQUE (bundle_id, ordinal)
);

-- Tie a scored evidence event to the bundle it came from so a single bundle's
-- exact event set can be replayed for verification. ``ordinal`` preserves the
-- event's position within its bundle so the bundle reconstructs faithfully (the
-- engine hash is order-independent, but the bundle round-trip is not). Both are
-- additive + nullable: legacy events (and events stored outside a bundle) keep
-- ``bundle_id`` / ``ordinal`` NULL.
ALTER TABLE research.evidence_events
    ADD COLUMN IF NOT EXISTS bundle_id uuid REFERENCES research.evidence_bundle(bundle_id);
ALTER TABLE research.evidence_events
    ADD COLUMN IF NOT EXISTS ordinal integer;
CREATE INDEX IF NOT EXISTS idx_evidence_bundle ON research.evidence_events (bundle_id);

COMMIT;
