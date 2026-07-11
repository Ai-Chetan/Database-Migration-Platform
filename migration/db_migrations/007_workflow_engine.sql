-- ============================================================
-- Migration Platform Kernel — Part 2: Workflow Engine
-- File: migration/backend/workflow_engine/db_migrations/007_workflow_engine.sql
--
-- Run on your PostgreSQL metadata database:
--   psql -U postgres -d migration_metadata -f 007_workflow_engine.sql
-- ============================================================


-- ── 1. Workflow Definitions ───────────────────────────────────────────────────
-- Stores named, versioned, serializable workflow definitions.
-- A WorkflowDefinition is a DAG of WorkflowNodes with edges and config.
-- The default "standard_migration" workflow is seeded below.

CREATE TABLE IF NOT EXISTS workflow_definitions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       VARCHAR(100) NOT NULL DEFAULT 'global',
    name            VARCHAR(255) NOT NULL,
    version         VARCHAR(50)  NOT NULL DEFAULT '1.0.0',
    description     TEXT,
    nodes           JSONB NOT NULL DEFAULT '[]',
    edges           JSONB NOT NULL DEFAULT '[]',
    -- nodes: [{id, node_type, label, config, retry_policy, timeout_seconds, parallelizable}]
    -- edges: [{from_node_id, to_node_id, condition}]  condition: "always"|"on_success"|"on_failure"
    is_default      BOOLEAN DEFAULT FALSE,   -- one default per tenant
    is_active       BOOLEAN DEFAULT TRUE,
    created_at      TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_workflow_def_name_version
    ON workflow_definitions (tenant_id, name, version);
CREATE INDEX IF NOT EXISTS idx_workflow_def_active
    ON workflow_definitions (tenant_id, is_active);


-- ── 2. Workflow Executions ────────────────────────────────────────────────────
-- One row per chunk execution — replaces what ChunkExecutor used to own.
-- Each execution runs the workflow definition against one chunk's data.

CREATE TABLE IF NOT EXISTS workflow_executions (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workflow_def_id     UUID REFERENCES workflow_definitions(id),
    job_id              UUID REFERENCES migration_jobs(id) ON DELETE CASCADE,
    chunk_id            UUID REFERENCES migration_chunks(id) ON DELETE CASCADE,
    worker_id           VARCHAR(100),
    status              VARCHAR(50)  DEFAULT 'pending',
    -- pending | running | completed | failed | retrying | skipped
    started_at          TIMESTAMP,
    completed_at        TIMESTAMP,
    duration_ms         INTEGER,
    rows_read           BIGINT DEFAULT 0,
    rows_written        BIGINT DEFAULT 0,
    rows_skipped        BIGINT DEFAULT 0,
    current_node        VARCHAR(100),   -- which node is executing right now
    context_snapshot    JSONB DEFAULT '{}',  -- last context state (for resume/debug)
    error_message       TEXT,
    error_node          VARCHAR(100),   -- which node failed
    retry_count         INTEGER DEFAULT 0,
    created_at          TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_wf_exec_job     ON workflow_executions (job_id, status);
CREATE INDEX IF NOT EXISTS idx_wf_exec_chunk   ON workflow_executions (chunk_id);
CREATE INDEX IF NOT EXISTS idx_wf_exec_worker  ON workflow_executions (worker_id, status);


-- ── 3. Workflow Node Execution Log ────────────────────────────────────────────
-- Granular per-node record within one workflow execution.
-- Enables "which node failed and what was the input context?" debugging.

CREATE TABLE IF NOT EXISTS workflow_node_log (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    execution_id    UUID NOT NULL REFERENCES workflow_executions(id) ON DELETE CASCADE,
    node_id         VARCHAR(100) NOT NULL,   -- matches nodes[].id in workflow_definitions
    node_type       VARCHAR(100) NOT NULL,   -- ReadNode | TransformNode | ValidateNode | ...
    status          VARCHAR(50)  DEFAULT 'pending',
    started_at      TIMESTAMP,
    completed_at    TIMESTAMP,
    duration_ms     INTEGER,
    input_summary   JSONB DEFAULT '{}',  -- lightweight: {"rows": 5000, "table": "users"}
    output_summary  JSONB DEFAULT '{}',  -- {"rows_written": 4998, "rows_skipped": 2}
    error_message   TEXT,
    retry_count     INTEGER DEFAULT 0,
    created_at      TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_wf_node_log_exec ON workflow_node_log (execution_id);


-- ── 4. Seed the default standard migration workflow ───────────────────────────
-- This is the exact equivalent of what ChunkExecutor.execute() used to hardcode,
-- now expressed as a portable, serializable, inspectable workflow definition.

INSERT INTO workflow_definitions (
    id, tenant_id, name, version, description,
    nodes, edges, is_default, is_active, created_at, updated_at
) VALUES (
    gen_random_uuid(),
    'global',
    'standard_migration',
    '1.0.0',
    'Default migration workflow: read → transform → validate → write → verify → notify → metrics → audit',
    '[
        {"id": "read",      "node_type": "ReadNode",      "label": "Read Source Data",
         "config": {},
         "retry_policy": {"max_retries": 3, "backoff_seconds": 5, "backoff_multiplier": 2},
         "timeout_seconds": 300, "parallelizable": false},

        {"id": "transform", "node_type": "TransformNode", "label": "Apply Column Mappings",
         "config": {},
         "retry_policy": {"max_retries": 1, "backoff_seconds": 0, "backoff_multiplier": 1},
         "timeout_seconds": 120, "parallelizable": true},

        {"id": "validate",  "node_type": "ValidateNode",  "label": "Pre-Write Validation",
         "config": {"check_not_null": true, "check_types": true},
         "retry_policy": {"max_retries": 1, "backoff_seconds": 0, "backoff_multiplier": 1},
         "timeout_seconds": 60, "parallelizable": true},

        {"id": "write",     "node_type": "WriteNode",     "label": "Write to Target",
         "config": {"mode": "ignore_duplicates"},
         "retry_policy": {"max_retries": 3, "backoff_seconds": 10, "backoff_multiplier": 2},
         "timeout_seconds": 300, "parallelizable": false},

        {"id": "verify",    "node_type": "VerifyNode",    "label": "Row Count Verification",
         "config": {"verify_row_count": true, "verify_checksum": true},
         "retry_policy": {"max_retries": 2, "backoff_seconds": 5, "backoff_multiplier": 1},
         "timeout_seconds": 120, "parallelizable": false},

        {"id": "metrics",   "node_type": "MetricsNode",   "label": "Record Metrics",
         "config": {},
         "retry_policy": {"max_retries": 1, "backoff_seconds": 0, "backoff_multiplier": 1},
         "timeout_seconds": 30,  "parallelizable": true},

        {"id": "notify",    "node_type": "NotifyNode",    "label": "Publish Events",
         "config": {},
         "retry_policy": {"max_retries": 2, "backoff_seconds": 5, "backoff_multiplier": 1},
         "timeout_seconds": 30,  "parallelizable": true},

        {"id": "audit",     "node_type": "AuditNode",     "label": "Write Audit Trail",
         "config": {},
         "retry_policy": {"max_retries": 2, "backoff_seconds": 5, "backoff_multiplier": 1},
         "timeout_seconds": 30,  "parallelizable": true}
    ]'::jsonb,
    '[
        {"from": "read",      "to": "transform", "condition": "on_success"},
        {"from": "transform", "to": "validate",  "condition": "on_success"},
        {"from": "validate",  "to": "write",     "condition": "on_success"},
        {"from": "write",     "to": "verify",    "condition": "on_success"},
        {"from": "verify",    "to": "metrics",   "condition": "on_success"},
        {"from": "metrics",   "to": "notify",    "condition": "always"},
        {"from": "notify",    "to": "audit",     "condition": "always"}
    ]'::jsonb,
    TRUE, TRUE, NOW(), NOW()
) ON CONFLICT DO NOTHING;
