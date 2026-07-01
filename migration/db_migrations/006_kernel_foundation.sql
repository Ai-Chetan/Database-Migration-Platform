-- ============================================================
-- Migration Platform Kernel — Part 1: Kernel Foundation
-- File: migration/backend/kernel/db_migrations/006_kernel_foundation.sql
--
-- Creates: plugin_registry, event_log, service_registry, metadata_catalog
--
-- Run on your PostgreSQL metadata database:
--   psql -U postgres -d migration_metadata -f 006_kernel_foundation.sql
-- ============================================================


-- ── 1. Plugin Registry ────────────────────────────────────────────────────────
-- Generalizes connector_registry into a universal plugin registry.
-- Every plugin type (connector, validator, transformer, notifier, etc.)
-- registers itself here.

CREATE TABLE IF NOT EXISTS plugin_registry (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       VARCHAR(100) DEFAULT 'global',   -- 'global' = built-in, else tenant-specific
    plugin_type     VARCHAR(50) NOT NULL,
    -- connector | validator | transformer | notifier | assessment | scheduler
    -- | policy | report | ai | storage | security | monitoring
    name            VARCHAR(100) NOT NULL,            -- "mysql", "row_count_validator"
    display_name    VARCHAR(255) NOT NULL,
    version         VARCHAR(50) DEFAULT '1.0.0',
    capabilities    JSONB DEFAULT '[]',
    config_schema   JSONB DEFAULT '{}',               -- JSON Schema for plugin config
    module_path     VARCHAR(500),                     -- python import path, e.g.
                                                        -- "backend.connector_framework.connectors.mysql.mysql_connector.MySQLConnector"
    is_active       BOOLEAN DEFAULT TRUE,
    is_builtin      BOOLEAN DEFAULT TRUE,
    metadata        JSONB DEFAULT '{}',
    created_at      TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_plugin_registry_unique
    ON plugin_registry (tenant_id, plugin_type, name);
CREATE INDEX IF NOT EXISTS idx_plugin_registry_type
    ON plugin_registry (plugin_type, is_active);


-- ── 2. Event Log ──────────────────────────────────────────────────────────────
-- Persistent log of every event published on the Event Bus.
-- Redis Pub/Sub handles real-time delivery; this table is the durable record
-- so subscribers that were offline can replay, and for audit/debugging.

CREATE TABLE IF NOT EXISTS event_log (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       VARCHAR(100) DEFAULT 'local',
    event_type      VARCHAR(150) NOT NULL,    -- "job.started", "chunk.completed", "drift.detected"
    source_service  VARCHAR(100),             -- which microservice emitted this
    resource_type   VARCHAR(100),             -- "job" | "chunk" | "table" | "worker"
    resource_id     VARCHAR(255),
    payload         JSONB DEFAULT '{}',
    correlation_id  VARCHAR(255),             -- ties related events together (e.g. job_id)
    published_at    TIMESTAMP NOT NULL DEFAULT NOW(),
    delivered_count INTEGER DEFAULT 0          -- how many subscribers received it
);

CREATE INDEX IF NOT EXISTS idx_event_log_type        ON event_log (event_type, published_at DESC);
CREATE INDEX IF NOT EXISTS idx_event_log_correlation ON event_log (correlation_id);
CREATE INDEX IF NOT EXISTS idx_event_log_resource    ON event_log (resource_type, resource_id);
CREATE INDEX IF NOT EXISTS idx_event_log_tenant      ON event_log (tenant_id, published_at DESC);


-- ── 3. Event Subscriptions ────────────────────────────────────────────────────
-- Tracks which services/handlers are subscribed to which event types.
-- Mostly for introspection/debugging — actual delivery is via Redis Pub/Sub.

CREATE TABLE IF NOT EXISTS event_subscriptions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    subscriber_name VARCHAR(150) NOT NULL,    -- "notification_service", "knowledge_base"
    event_pattern   VARCHAR(150) NOT NULL,    -- "job.*", "chunk.completed", "*"
    handler_path    VARCHAR(500),             -- optional: python callable path
    is_active       BOOLEAN DEFAULT TRUE,
    created_at      TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_event_subs_pattern ON event_subscriptions (event_pattern, is_active);


-- ── 4. Service Registry ───────────────────────────────────────────────────────
-- Tracks all microservices in the platform: where they live, health, version.
-- Replaces hardcoded port references scattered across services.

CREATE TABLE IF NOT EXISTS service_registry (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    service_name    VARCHAR(100) UNIQUE NOT NULL,   -- "control_plane", "worker_service"
    display_name    VARCHAR(255) NOT NULL,
    base_url        VARCHAR(500) NOT NULL,           -- "http://localhost:8000"
    health_endpoint VARCHAR(255) DEFAULT '/health',
    version         VARCHAR(50),
    status          VARCHAR(50) DEFAULT 'unknown',   -- healthy | degraded | down | unknown
    last_heartbeat  TIMESTAMP,
    metadata        JSONB DEFAULT '{}',              -- e.g. {"capabilities": [...]}
    registered_at   TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_service_registry_status ON service_registry (status);

-- Seed the known microservices built so far
INSERT INTO service_registry (service_name, display_name, base_url, version, status) VALUES
('control_plane',          'Control Plane',                      'http://localhost:8000', '1.0.0', 'unknown'),
('monitoring_service',     'Monitoring Service',                 'http://localhost:8001', '1.0.0', 'unknown'),
('schema_mapping_service',  'Schema Mapping Service',             'http://localhost:8003', '2.0.0', 'unknown'),
('enterprise_execution',   'Enterprise Execution Engine',        'http://localhost:8004', '1.0.0', 'unknown'),
('enterprise_security',    'Enterprise Security & SaaS',         'http://localhost:8005', '1.0.0', 'unknown'),
('connector_framework_cdc','Connector Framework & CDC Engine',   'http://localhost:8006', '1.0.0', 'unknown'),
('platform_kernel',        'Migration Platform Kernel',          'http://localhost:8007', '1.0.0', 'unknown')
ON CONFLICT (service_name) DO NOTHING;


-- ── 5. Metadata Catalog ───────────────────────────────────────────────────────
-- Central store for everything the Metadata Intelligence Layer (Part 3) will
-- write and everything downstream (Assessment, Advisor, Workflow Engine,
-- Adaptive Chunk Planner) will read. Created now as the contract; populated later.

CREATE TABLE IF NOT EXISTS metadata_catalog (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       VARCHAR(100) DEFAULT 'local',
    connection_id   UUID,                     -- references connection_registry(id)
    schema_version_id UUID,                   -- references schema_versions(id)
    table_name      VARCHAR(255) NOT NULL,
    catalog_type    VARCHAR(100) NOT NULL,
    -- statistics | relationship | distribution | lob_detection | compression
    -- | hot_cold_classification | growth_rate
    data            JSONB NOT NULL DEFAULT '{}',
    -- Example for catalog_type='statistics':
    --   {"row_count": 2700000000, "size_bytes": 2900000000000,
    --    "avg_row_bytes": 1074, "growth_rate_pct_per_month": 3.2}
    -- Example for catalog_type='hot_cold_classification':
    --   {"classification": "hot", "read_freq": "high", "write_freq": "high"}
    computed_at     TIMESTAMP NOT NULL DEFAULT NOW(),
    expires_at      TIMESTAMP,                 -- stats go stale; NULL = never expires
    created_at      TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_metadata_catalog_table
    ON metadata_catalog (connection_id, table_name, catalog_type);
CREATE INDEX IF NOT EXISTS idx_metadata_catalog_tenant
    ON metadata_catalog (tenant_id, catalog_type);
CREATE INDEX IF NOT EXISTS idx_metadata_catalog_fresh
    ON metadata_catalog (table_name, computed_at DESC);


-- ── 6. Seed built-in plugins from existing connector framework ───────────────
-- Mirrors what's already in connector_registry so the new generalized
-- plugin_registry has them too, without breaking the old table.

INSERT INTO plugin_registry
    (tenant_id, plugin_type, name, display_name, version, capabilities, module_path, is_builtin)
VALUES
('global', 'connector', 'mysql', 'MySQL', '1.0.0',
 '["discover","stream_read","bulk_write","cdc","checksum","constraints","indexes"]'::jsonb,
 'backend.connector_framework.connectors.mysql.mysql_connector.MySQLConnector', TRUE),

('global', 'connector', 'postgresql', 'PostgreSQL', '1.0.0',
 '["discover","stream_read","bulk_write","cdc","checksum","constraints","indexes","jsonb"]'::jsonb,
 'backend.connector_framework.connectors.postgresql.postgresql_connector.PostgreSQLConnector', TRUE),

('global', 'connector', 'sqlite', 'SQLite', '1.0.0',
 '["discover","stream_read","bulk_write","checksum"]'::jsonb,
 'backend.connector_framework.connectors.sqlite.sqlite_connector.SQLiteConnector', TRUE)

ON CONFLICT (tenant_id, plugin_type, name) DO NOTHING;
