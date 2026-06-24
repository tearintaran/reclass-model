-- Evidence workbench, coverage tracking, and curation queues (job1).
--
-- Three concerns, split across the two data domains by the same boundary the rest
-- of the schema enforces:
--
--   * research.reviewer_evidence  — DE-IDENTIFIED reviewer/pipeline-entered evidence,
--     keyed only on the public ``variant_key`` (no patient/tenant identifier, no join
--     back to clinical). This is the workbench persistence for criteria a public score
--     does not encode (PVS1/LoF, PS3/BS3, PM3, PP1/BS4, PP4, PS4, BA1/BS1). It mirrors
--     research.evidence_events but adds the reviewer-provenance an audit needs:
--     source version, checksum, access date, the reviewer who entered it, and the
--     expiry / re-review metadata that drives periodic re-review. ``points`` is kept
--     NULL for strength-derived evidence (faithful to research.evidence_events) so a
--     reconstruction stays byte-identical.
--
--   * clinical.evidence_coverage  — TENANT-SCOPED, RLS-protected coverage roll-up:
--     which ACMG criteria are present vs. missing for a (tenant, variant), and the
--     gene / VCEP / disease / variant-class / provider context an operator slices by
--     to see which cases are blocked by missing evidence. Tenant-owned operational
--     metadata, like clinical.reanalysis_queue.
--
--   * clinical.curation_queue     — TENANT-SCOPED, RLS-protected work items the
--     workbench surfaces for human curation: unmatched / ambiguous ClinGen-ClinVar
--     identities, missing transcript context, missing cohort denominators, and
--     unresolved pathogenic-vs-benign conflicts. This job only SURFACES them; the
--     resolution policy lives in Job 2.
--
-- Applied by db/apply.py after db/schema.sql. Keep migration files free of explicit
-- BEGIN/COMMIT so the apply tool can wrap SQL and ledger writes in one transaction.

-- --------------------------------------------------------------------------- --
-- RESEARCH: reviewer/pipeline-entered structured evidence (de-identified)      --
-- --------------------------------------------------------------------------- --
CREATE TABLE IF NOT EXISTS research.reviewer_evidence (
    reviewer_evidence_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    variant_key      text NOT NULL REFERENCES research.variant(variant_key),
    acmg_criterion   text NOT NULL,
    direction        evidence_direction NOT NULL,
    applied_strength text,
    points           numeric,                     -- NULL for strength-derived (faithful)
    source           text NOT NULL,               -- reviewer-supplied source label / citation
    source_version   text,
    source_url       text,
    checksum         text,                         -- content hash of the entered record
    checksum_algorithm text NOT NULL DEFAULT 'sha256',
    access_date      date,                         -- when the source was read
    reviewer         text NOT NULL,                -- curator identity (provenance, NOT patient PHI)
    reviewer_credential text,
    status           text NOT NULL DEFAULT 'active', -- active|expired|superseded|withdrawn
    notes            text,
    entered_at       timestamptz NOT NULL DEFAULT now(),
    expires_at       timestamptz,                  -- re-review deadline; NULL = no expiry
    re_review_at     timestamptz,                  -- when the entry was last re-reviewed
    CHECK (status IN ('active', 'expired', 'superseded', 'withdrawn'))
);
CREATE INDEX IF NOT EXISTS idx_reviewer_evidence_variant
    ON research.reviewer_evidence (variant_key);
CREATE INDEX IF NOT EXISTS idx_reviewer_evidence_status
    ON research.reviewer_evidence (status);
CREATE INDEX IF NOT EXISTS idx_reviewer_evidence_expiry
    ON research.reviewer_evidence (expires_at)
    WHERE expires_at IS NOT NULL;

-- --------------------------------------------------------------------------- --
-- CLINICAL: evidence-coverage roll-up (tenant-scoped, RLS)                     --
-- --------------------------------------------------------------------------- --
CREATE TABLE IF NOT EXISTS clinical.evidence_coverage (
    coverage_id      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id        uuid NOT NULL REFERENCES clinical.tenant(tenant_id),
    variant_key      text NOT NULL,                -- public link; no FK across the boundary
    gene             text,
    vcep             text,
    disease          text,
    variant_class    text,                         -- snv | indel | cnv | splice | ...
    provider         text,                         -- primary evidence provider for the case
    present_criteria jsonb NOT NULL DEFAULT '[]'::jsonb,  -- criteria with evidence
    missing_criteria jsonb NOT NULL DEFAULT '[]'::jsonb,  -- expected-but-absent criteria
    blocked          boolean NOT NULL DEFAULT false,      -- blocked by missing evidence
    blocking_reason  text,
    updated_at       timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, variant_key)               -- one coverage row per (tenant, variant)
);
CREATE INDEX IF NOT EXISTS idx_coverage_tenant_blocked
    ON clinical.evidence_coverage (tenant_id, blocked);
CREATE INDEX IF NOT EXISTS idx_coverage_gene ON clinical.evidence_coverage (tenant_id, gene);

-- --------------------------------------------------------------------------- --
-- CLINICAL: curation work queue (tenant-scoped, RLS)                          --
-- --------------------------------------------------------------------------- --
CREATE TABLE IF NOT EXISTS clinical.curation_queue (
    curation_id      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id        uuid NOT NULL REFERENCES clinical.tenant(tenant_id),
    variant_key      text,
    kind             text NOT NULL,                -- unmatched_identity | ambiguous_identity |
                                                   -- missing_transcript | missing_cohort_denominator |
                                                   -- pathogenic_benign_conflict
    severity         text NOT NULL DEFAULT 'info', -- info | warning | blocker
    detail           jsonb NOT NULL DEFAULT '{}'::jsonb,
    state            text NOT NULL DEFAULT 'open', -- open | in_review | resolved | dismissed
    created_at       timestamptz NOT NULL DEFAULT now(),
    resolved_at      timestamptz,
    CHECK (kind IN ('unmatched_identity', 'ambiguous_identity', 'missing_transcript',
                    'missing_cohort_denominator', 'pathogenic_benign_conflict')),
    CHECK (severity IN ('info', 'warning', 'blocker')),
    CHECK (state IN ('open', 'in_review', 'resolved', 'dismissed'))
);
CREATE INDEX IF NOT EXISTS idx_curation_state ON clinical.curation_queue (tenant_id, state);
CREATE INDEX IF NOT EXISTS idx_curation_kind ON clinical.curation_queue (tenant_id, kind);
-- At most one OUTSTANDING (open) item per (tenant, variant, kind): re-surfacing the
-- same gap is a no-op, so a noisy scan cannot flood the queue.
CREATE UNIQUE INDEX IF NOT EXISTS uq_curation_open
    ON clinical.curation_queue (tenant_id, variant_key, kind)
    WHERE state = 'open';

-- Row-level security: a session sees only its own tenant's coverage / curation rows.
ALTER TABLE clinical.evidence_coverage ENABLE ROW LEVEL SECURITY;
ALTER TABLE clinical.curation_queue    ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'tenant_isolation_evidence_coverage') THEN
        CREATE POLICY tenant_isolation_evidence_coverage ON clinical.evidence_coverage
            USING (tenant_id = current_setting('app.current_tenant', true)::uuid);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'tenant_isolation_curation_queue') THEN
        CREATE POLICY tenant_isolation_curation_queue ON clinical.curation_queue
            USING (tenant_id = current_setting('app.current_tenant', true)::uuid);
    END IF;
END$$;
