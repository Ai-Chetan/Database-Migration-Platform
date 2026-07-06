-- ============================================================
-- Migration Platform Kernel — Part 7: Data Masking + Synthetic Data
-- File: migration/backend/masking/db_migrations/012_masking.sql
--
-- Run on your PostgreSQL metadata database:
--   psql -U postgres -d migration_metadata -f 012_masking.sql
-- ============================================================


-- ── 1. Masking Rule Sets ──────────────────────────────────────────────────────
-- A masking rule set is a named, reusable collection of column-level rules
-- that can be applied to any migration project.

CREATE TABLE IF NOT EXISTS masking_rule_sets (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       VARCHAR(100) NOT NULL DEFAULT 'local',
    name            VARCHAR(255) NOT NULL,
    description     TEXT,
    is_active       BOOLEAN DEFAULT TRUE,
    created_at      TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_masking_rule_sets_name
    ON masking_rule_sets (tenant_id, name);


-- ── 2. Masking Rules ──────────────────────────────────────────────────────────
-- One rule per column. Multiple rules can be in one rule set.

CREATE TABLE IF NOT EXISTS masking_rules (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    rule_set_id     UUID REFERENCES masking_rule_sets(id) ON DELETE CASCADE,
    table_name      VARCHAR(255) NOT NULL,
    column_name     VARCHAR(255) NOT NULL,
    strategy        VARCHAR(100) NOT NULL,
    -- hash | redact | partial | encrypt | fake_name | fake_email | fake_phone
    -- | fake_address | fake_date | fake_ssn | fake_credit_card | nullify
    -- | fixed_value | format_preserve
    strategy_config JSONB DEFAULT '{}',
    -- hash:          {"algorithm": "sha256"}
    -- partial:       {"keep_start": 2, "keep_end": 4, "mask_char": "*"}
    -- encrypt:       {"reversible": true}
    -- fake_*:        {"locale": "en_US", "seed_column": "id"}
    -- fixed_value:   {"value": "REDACTED"}
    -- format_preserve: {"format": "XXX-XX-XXXX"}
    is_active       BOOLEAN DEFAULT TRUE,
    created_at      TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_masking_rules_set
    ON masking_rules (rule_set_id);
CREATE INDEX IF NOT EXISTS idx_masking_rules_table
    ON masking_rules (table_name, column_name);


-- ── 3. Masking Job Log ────────────────────────────────────────────────────────
-- Tracks when masking was applied to a migration job and how many
-- values were transformed.

CREATE TABLE IF NOT EXISTS masking_job_log (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id          UUID REFERENCES migration_jobs(id) ON DELETE CASCADE,
    rule_set_id     UUID REFERENCES masking_rule_sets(id),
    table_name      VARCHAR(255),
    column_name     VARCHAR(255),
    strategy        VARCHAR(100),
    rows_masked     BIGINT DEFAULT 0,
    rows_skipped    BIGINT DEFAULT 0,
    applied_at      TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_masking_log_job
    ON masking_job_log (job_id);


-- ── 4. Extend schema_column_mappings with masking kinds ───────────────────────
-- Add 'mask' and 'synthesize' as valid mapping_kind values.
-- No schema change needed — mapping_kind is already VARCHAR and
-- mapping_config is already JSONB. This is documentation only.

-- mapping_kind = 'mask':
--   mapping_config = {"strategy": "hash", "algorithm": "sha256"}
--   mapping_config = {"strategy": "partial", "keep_start": 2, "keep_end": 4}
--   mapping_config = {"strategy": "encrypt", "reversible": false}

-- mapping_kind = 'synthesize':
--   mapping_config = {"generator": "fake_email", "locale": "en_US", "seed_column": "id"}
--   mapping_config = {"generator": "fake_name",  "locale": "en_US"}
