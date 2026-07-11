-- ============================================================
-- Priority 9 — Schema Mapping Service
-- Database Migration SQL
-- File: migration/backend/schema_mapping_service/db_migrations/001_schema_mapping_tables.sql
--
-- Run this on your local PostgreSQL metadata database:
--   psql -U postgres -d migration_metadata -f 001_schema_mapping_tables.sql
-- ============================================================

-- ── 1. Schema Versions ────────────────────────────────────────────────────────
-- Stores every discovered or imported schema snapshot.
-- Every time you discover a live DB or import a file, a new version is saved.

CREATE TABLE IF NOT EXISTS schema_versions (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id      VARCHAR(100) NOT NULL DEFAULT 'local',
    name           VARCHAR(255) NOT NULL,          -- e.g. "production_mysql_2026"
    db_type        VARCHAR(50)  NOT NULL,          -- mysql | postgresql | oracle
    version_label  VARCHAR(100),                   -- "v1", "before_migration", etc.
    schema_data    JSONB        NOT NULL,          -- Full discovered schema as JSON
    source_type    VARCHAR(50)  DEFAULT 'live_db', -- live_db | file | ddl_import
    notes          TEXT,
    created_at     TIMESTAMP    NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_schema_versions_tenant ON schema_versions (tenant_id);
CREATE INDEX IF NOT EXISTS idx_schema_versions_name   ON schema_versions (tenant_id, name);


-- ── 2. Mapping Projects ───────────────────────────────────────────────────────
-- One project ties a source schema to a target schema.
-- All table/column mappings belong to a project.

CREATE TABLE IF NOT EXISTS mapping_projects (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id          VARCHAR(100) NOT NULL DEFAULT 'local',
    name               VARCHAR(255) NOT NULL,
    description        TEXT,
    source_schema_id   UUID REFERENCES schema_versions(id),
    target_schema_id   UUID REFERENCES schema_versions(id),
    status             VARCHAR(50)  DEFAULT 'draft',  -- draft | ready | executing | done
    dry_run_result     JSONB,                          -- Latest dry-run output
    migration_plan     JSONB,                          -- Generated execution plan
    created_at         TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at         TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_mapping_projects_tenant ON mapping_projects (tenant_id);


-- ── 3. Table Mappings ─────────────────────────────────────────────────────────
-- How source table(s) map to target table(s).
-- Supports: single | split | merge | graph

CREATE TABLE IF NOT EXISTS schema_table_mappings (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id      UUID NOT NULL REFERENCES mapping_projects(id) ON DELETE CASCADE,
    mapping_type    VARCHAR(20) NOT NULL DEFAULT 'single',  -- single|split|merge|graph
    source_tables   JSONB NOT NULL,    -- ["users"] or ["users","profiles"]
    target_tables   JSONB NOT NULL,    -- ["customers"] or ["cust","cust_addr"]
    join_condition  TEXT,              -- SQL JOIN clause for merge
    notes           TEXT,
    created_at      TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_table_mappings_project ON schema_table_mappings (project_id);


-- ── 4. Column Mappings ────────────────────────────────────────────────────────
-- How each source column maps to a target column.
-- mapping_kind: direct | rename | transform | constant | expression | lookup

CREATE TABLE IF NOT EXISTS schema_column_mappings (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    table_mapping_id   UUID NOT NULL REFERENCES schema_table_mappings(id) ON DELETE CASCADE,

    -- Source
    source_table       VARCHAR(255),
    source_column      VARCHAR(255),
    source_type        VARCHAR(100),

    -- Target
    target_table       VARCHAR(255),
    target_column      VARCHAR(255),
    target_type        VARCHAR(100),

    -- Mapping kind and config
    mapping_kind       VARCHAR(50) DEFAULT 'direct',  -- direct|rename|transform|constant|expression|lookup
    mapping_config     JSONB,  -- {"expression": "...", "value": "...", "table": "...", "sql": "..."}

    -- Conversion analysis (computed by type engine, stored for display)
    conversion_safety  VARCHAR(20),  -- safe | lossy | unsafe | conditional
    requires_cast      BOOLEAN DEFAULT FALSE,
    cast_expression    VARCHAR(500),  -- e.g. "CAST(col AS BIGINT)"

    created_at         TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_col_mappings_table_mapping ON schema_column_mappings (table_mapping_id);


-- ── 5. Recommendation Results ─────────────────────────────────────────────────
-- Stores auto-generated mapping suggestions from the recommendation engine.
-- User can accept (accepted=true) or reject (accepted=false) each one.

CREATE TABLE IF NOT EXISTS schema_recommendations (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id  UUID NOT NULL REFERENCES mapping_projects(id) ON DELETE CASCADE,
    rec_type    VARCHAR(50),     -- table_match | column_match | rename_candidate
    source_ref  VARCHAR(500),    -- "users" or "users.first_name"
    target_ref  VARCHAR(500),    -- "customers" or "customers.full_name"
    confidence  FLOAT,           -- 0.0 - 1.0
    reason      VARCHAR(200),    -- exact_match | fuzzy_0.87 | alias_match
    accepted    BOOLEAN,         -- NULL=pending, TRUE=accepted, FALSE=rejected
    created_at  TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_recommendations_project ON schema_recommendations (project_id);


-- ── 6. Data Type Conversion Rules ─────────────────────────────────────────────
-- Configurable conversion matrix — NOT hardcoded.
-- System defaults (is_system=true) can be overridden per tenant.

CREATE TABLE IF NOT EXISTS datatype_conversion_rules (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id     VARCHAR(100) DEFAULT 'global',   -- 'global' = system default
    source_db     VARCHAR(50)  DEFAULT 'any',       -- mysql | postgresql | any
    target_db     VARCHAR(50)  DEFAULT 'any',
    source_type   VARCHAR(100) NOT NULL,
    target_type   VARCHAR(100) NOT NULL,
    safety        VARCHAR(20)  NOT NULL,            -- safe | lossy | unsafe | conditional
    cast_template VARCHAR(500),                     -- "CAST({col} AS {type})"
    notes         TEXT,
    is_system     BOOLEAN DEFAULT TRUE
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_dtype_rules_unique
    ON datatype_conversion_rules (tenant_id, source_db, target_db, source_type, target_type);


-- ── 7. Generated Scripts ──────────────────────────────────────────────────────
-- Stores every generated migration script so it can be downloaded via API.

CREATE TABLE IF NOT EXISTS generated_scripts (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id   UUID NOT NULL REFERENCES mapping_projects(id) ON DELETE CASCADE,
    script_type  VARCHAR(50),      -- python | sql | spark | airflow_dag
    target_table VARCHAR(255),
    content      TEXT NOT NULL,
    filename     VARCHAR(500),
    created_at   TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_generated_scripts_project ON generated_scripts (project_id);


-- ── 8. Schema Validation Results ─────────────────────────────────────────────
-- Detailed validation results per table mapping (row count, checksum, sample).

CREATE TABLE IF NOT EXISTS schema_validation_results (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id       UUID NOT NULL REFERENCES mapping_projects(id) ON DELETE CASCADE,
    validation_type  VARCHAR(100),   -- row_count | checksum | sample | business_rule
    source_table     VARCHAR(255),
    target_table     VARCHAR(255),
    source_value     TEXT,
    target_value     TEXT,
    passed           BOOLEAN,
    details          TEXT,
    created_at       TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_schema_val_project ON schema_validation_results (project_id);


-- ── 9. Seed default data type conversion rules ────────────────────────────────

INSERT INTO datatype_conversion_rules
    (source_type, target_type, source_db, target_db, safety, cast_template, notes, is_system)
VALUES
-- ── Same-type (always safe) ──────────────────────────────────────────────────
('int',       'int',       'any','any','safe',    NULL, 'Same type',NULL),
('bigint',    'bigint',    'any','any','safe',    NULL, 'Same type',NULL),
('varchar',   'varchar',   'any','any','safe',    NULL, 'Same type',NULL),
('text',      'text',      'any','any','safe',    NULL, 'Same type',NULL),
('date',      'date',      'any','any','safe',    NULL, 'Same type',NULL),
('datetime',  'datetime',  'any','any','safe',    NULL, 'Same type',NULL),
('timestamp', 'timestamp', 'any','any','safe',    NULL, 'Same type',NULL),
('json',      'json',      'any','any','safe',    NULL, 'Same type',NULL),
('jsonb',     'jsonb',     'any','any','safe',    NULL, 'Same type',NULL),

-- ── Integer widening (safe) ──────────────────────────────────────────────────
('tinyint',  'smallint',  'any','any','safe', 'CAST({col} AS SMALLINT)', 'Integer widening', NULL),
('tinyint',  'int',       'any','any','safe', 'CAST({col} AS INT)',      'Integer widening', NULL),
('tinyint',  'bigint',    'any','any','safe', 'CAST({col} AS BIGINT)',   'Integer widening', NULL),
('smallint', 'int',       'any','any','safe', 'CAST({col} AS INT)',      'Integer widening', NULL),
('smallint', 'bigint',    'any','any','safe', 'CAST({col} AS BIGINT)',   'Integer widening', NULL),
('int',      'bigint',    'any','any','safe', 'CAST({col} AS BIGINT)',   'Integer widening', NULL),
('integer',  'bigint',    'any','any','safe', 'CAST({col} AS BIGINT)',   'Integer widening', NULL),

-- ── Integer narrowing (lossy) ─────────────────────────────────────────────────
('bigint',  'int',       'any','any','lossy', 'CAST({col} AS SIGNED)',  'May overflow', NULL),
('bigint',  'smallint',  'any','any','lossy', 'CAST({col} AS SIGNED)',  'May overflow', NULL),
('int',     'smallint',  'any','any','lossy', 'CAST({col} AS SIGNED)',  'May overflow', NULL),
('int',     'tinyint',   'any','any','lossy', 'CAST({col} AS SIGNED)',  'May overflow', NULL),

-- ── Float/Decimal (lossy) ─────────────────────────────────────────────────────
('float',   'int',     'any','any','lossy', 'CAST({col} AS SIGNED)',            'Truncates decimal', NULL),
('double',  'int',     'any','any','lossy', 'CAST({col} AS SIGNED)',            'Truncates decimal', NULL),
('float',   'decimal', 'any','any','lossy', 'CAST({col} AS DECIMAL(18,4))',     'Precision loss possible', NULL),
('decimal', 'float',   'any','any','lossy', 'CAST({col} AS DOUBLE)',            'Precision loss possible', NULL),
('int',     'decimal', 'any','any','safe',  'CAST({col} AS DECIMAL(18,0))',     'Safe widening', NULL),

-- ── String conversions ────────────────────────────────────────────────────────
('varchar',  'text',     'any','any','safe',  NULL,                             'String widening', NULL),
('char',     'varchar',  'any','any','safe',  NULL,                             'String widening', NULL),
('tinytext', 'text',     'any','any','safe',  NULL,                             'String widening', NULL),
('text',     'varchar',  'any','any','lossy', 'CAST({col} AS CHAR(255))',        'May truncate', NULL),
('int',      'varchar',  'any','any','safe',  'CAST({col} AS CHAR)',             'Numeric to string', NULL),
('bigint',   'varchar',  'any','any','safe',  'CAST({col} AS CHAR)',             'Numeric to string', NULL),

-- ── Date conversions ──────────────────────────────────────────────────────────
('date',      'datetime',   'any','any','safe',  'CAST({col} AS DATETIME)',      'Date to datetime', NULL),
('date',      'timestamp',  'any','any','safe',  'CAST({col} AS DATETIME)',      'Date to timestamp', NULL),
('datetime',  'date',       'any','any','lossy', 'DATE({col})',                  'Loses time part', NULL),
('timestamp', 'datetime',   'any','any','safe',  NULL,                           'Compatible types', NULL),
('datetime',  'timestamp',  'any','any','safe',  NULL,                           'Compatible types', NULL),
('datetime',  'varchar',    'any','any','safe',  'DATE_FORMAT({col},''%Y-%m-%d %H:%i:%s'')', 'Date to string', NULL),

-- ── JSON conversions ──────────────────────────────────────────────────────────
('json',    'text',    'any','any','safe',    'CAST({col} AS CHAR)',  'JSON to text', NULL),
('json',    'jsonb',   'any','any','safe',    NULL,                   'MySQL JSON to PG JSONB', NULL),
('jsonb',   'json',    'any','any','safe',    NULL,                   'PG JSONB to MySQL JSON', NULL),
('text',    'json',    'any','any','conditional', NULL,               'Only valid if text is valid JSON', NULL),
('varchar', 'json',    'any','any','conditional', NULL,               'Only valid if text is valid JSON', NULL),

-- ── Boolean conversions ───────────────────────────────────────────────────────
('tinyint', 'boolean', 'any','any','lossy', 'CASE WHEN {col}=1 THEN TRUE ELSE FALSE END', '0/1 to bool', NULL),
('boolean', 'tinyint', 'any','any','safe',  'CAST({col} AS UNSIGNED)', 'bool to 0/1', NULL),
('boolean', 'int',     'any','any','safe',  'CAST({col} AS UNSIGNED)', 'bool to 0/1', NULL),

-- ── Unsafe conversions ────────────────────────────────────────────────────────
('text',    'int',      'any','any','unsafe', NULL, 'Cannot auto-convert text to int', NULL),
('text',    'bigint',   'any','any','unsafe', NULL, 'Cannot auto-convert text to int', NULL),
('varchar', 'int',      'any','any','unsafe', NULL, 'Cannot auto-convert string to int', NULL),
('varchar', 'bigint',   'any','any','unsafe', NULL, 'Cannot auto-convert string to int', NULL),
('text',    'date',     'any','any','unsafe', NULL, 'Format unknown — manual conversion needed', NULL),
('text',    'datetime', 'any','any','unsafe', NULL, 'Format unknown — manual conversion needed', NULL),
('blob',    'varchar',  'any','any','unsafe', NULL, 'Binary to text may produce garbage', NULL),
('blob',    'int',      'any','any','unsafe', NULL, 'Binary to int not supported', NULL)

ON CONFLICT (tenant_id, source_db, target_db, source_type, target_type) DO NOTHING;
