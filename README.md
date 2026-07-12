# Migration Platform Kernel

**Enterprise-Grade Database Migration Engine**

A distributed, plugin-based database migration platform designed to compete with AWS DMS, Fivetran, Qlik Replicate, and Informatica. Built on Python, FastAPI, PostgreSQL, and Redis — fully open and self-hostable.

---

## Overview

Migration Platform Kernel is a production-grade data migration engine that orchestrates the complete lifecycle of a database migration: schema discovery, intelligence analysis, chunk-based parallel transfer, real-time validation, Change Data Capture (CDC) for near-zero downtime, and post-migration verification.

The platform is designed around a kernel architecture where a permanent core (Plugin Manager, Event Bus, Service Registry, Metadata Catalog) provides the foundation, and every capability above it — connectors, validators, transformers, notifiers, assessment engines, schedulers — is a replaceable plugin.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    MIGRATION PLATFORM KERNEL                     │
│  Plugin Manager │ Workflow Engine │ Event Bus │ Service Registry │
│                    Metadata Catalog                              │
└──────────────────────────┬──────────────────────────────────────┘
                           │
        ┌──────────────────┼──────────────────┐
        │                  │                  │
   CONNECTOR           INTELLIGENCE        OPERATIONS
   PLUGINS             LAYER               LAYER
   MySQL               Assessment          Operations
   PostgreSQL          Advisor             Console
   SQLite              Cost Estimator      Scheduler
   CSV/Parquet         Data Profiling      Reporting
   S3/Azure/GCS        Simulation          Knowledge Base
   REST API            Live Intelligence   Notifications
   Kafka               Schema Drift
                       Self-Tuning
                       Benchmarking
```

### Core Services

| Service | Port | Responsibility |
|---|---|---|
| Control Plane | 8000 | Job management, planning, chunk generation |
| Monitoring Service | 8001 | Observability, worker heartbeats, metrics |
| Schema Mapping Service | 8003 | Schema discovery, comparison, column mappings, dry-run |
| Enterprise Execution Engine | 8004 | Adaptive chunking, dependency graph, rollback |
| Enterprise Security | 8005 | JWT/RBAC, tenants, approvals, templates, secrets |
| Connector Framework + CDC | 8006 | Connector plugins, CDC engine, policy enforcement |
| Platform Kernel | 8007 | Plugin Manager, Event Bus, Service Registry, Metadata Catalog |
| Workflow Engine | 8008 | DAG-based execution kernel, node pipeline |
| Metadata Intelligence | 8009 | Statistics, relationships, distributions, LOB detection |
| Intelligence Service | 8010 | Assessment, Advisor, Cost Estimator, Data Quality Scanner |
| Simulation Engine | 8011 | What-if analysis, worker sweep, failure probability |
| Live Intelligence | 8012 | Schema drift detection, self-tuning, benchmarking |
| Data Masking | 8013 | Masking strategies, synthetic data generation |
| Plugin Service | 8014 | Validators, transformers, notifiers, policies |
| Extended Connectors | 8015 | File, Object Storage, REST API, Kafka |
| Operations Console | 8016 | Live worker/chunk/job control, maintenance mode |

### Workers

Workers are standalone Python processes (not FastAPI services) that pull chunks from a Redis queue and execute the Workflow Engine pipeline. Scale horizontally by running multiple worker processes:

```bash
WORKER_ID=worker-1 python -m backend.worker_service.app.worker
WORKER_ID=worker-2 python -m backend.worker_service.app.worker
```

---

## Key Capabilities

### Parallel Chunk-Based Migration
Data is divided into PK-range chunks and migrated in parallel across multiple workers. Each chunk is independently retryable, resumable, and verifiable. Adaptive chunk planning computes optimal chunk sizes per table based on row size, PK distribution, and LOB column presence.

### Workflow Engine as Execution Kernel
Every migration chunk executes through a configurable DAG of typed nodes:

```
ReadNode → TransformNode → [DataMaskingNode] → ValidateNode →
WriteNode → VerifyNode → MetricsNode → NotifyNode → AuditNode
```

Each node is independently retryable with configurable timeout and backoff. Custom workflows can be defined and versioned — operators can configure the pipeline without modifying core engine code.

### Change Data Capture (CDC)
Near-zero downtime migrations using native replication protocols:
- **MySQL**: Binary log (binlog) via `mysql-replication`
- **PostgreSQL**: WAL logical replication via `psycopg2`

CDC flow: capture start position → bulk load → replay accumulated changes → wait for lag < threshold → cutover in seconds rather than hours.

### Plugin Architecture
Every extension point in the platform follows the same pattern:

```python
# Adding a new database connector
class SnowflakeConnector(DatabaseConnector):
    def discover_schema(self): ...
    def stream_rows(self): ...
    def bulk_insert(self): ...

ConnectorRegistry.register("snowflake", SnowflakeConnector)
```

The same pattern applies to validators, transformers, notifiers, policies, workflow nodes, and assessment plugins. Zero changes to the core engine required.

### Pre-Migration Intelligence
Before a single row moves:
- **Assessment Engine**: Complexity (LOW/MEDIUM/HIGH/CRITICAL), risk level, estimated duration, recommended worker count
- **Migration Advisor**: Data-aware recommendations — detects PII columns by value pattern, flags missing FK indexes, recommends CDC for high-growth tables, identifies BIGINT→INT overflow by actual max values
- **Cost Estimator**: Real cloud compute/storage/network cost projections using actual table sizes from the Metadata Catalog
- **Data Quality Scanner**: Duplicate PKs, broken FKs, NULL violations, oversized values, MySQL zero-dates — before migration starts
- **Simulation Engine**: What-if projections for any worker/chunk configuration without touching production

### Data Masking and Synthetic Data
Production data never reaches non-production targets:
- **7 masking strategies**: hash (join-safe SHA-256), redact, partial, encrypt (AES reversible), nullify, fixed_value, format_preserve
- **20 synthetic generators**: fake_email, fake_name, fake_phone, fake_ssn, fake_credit_card, fake_address, and more
- All synthetic data is **deterministic**: the same source row always produces the same fake value, preserving referential integrity across tables
- `DataMaskingNode` integrates directly into the Workflow Engine pipeline

### Operations Console
Live manual control during active migrations:
- **Worker control**: pause/resume/kill/quarantine individual workers
- **Chunk control**: reassign stuck chunks, force retry, skip bad data ranges
- **Job control**: pause/resume/cancel jobs, rerun post-migration validation, live ETA
- **Maintenance mode**: block new jobs, drain workers for platform maintenance
- **Emergency stop**: immediate halt of all workers and jobs

### Schema Drift Detection
During long-running migrations, monitors the source database for DDL changes. On critical drift (column dropped, table dropped): automatically pauses the job and publishes a `drift.detected` event. On informational drift (column added, index changed): notifies without pausing.

### Self-Tuning Engine
Continuously adjusts worker count and chunk size during execution based on live metrics:
- Scales workers down when source/target CPU exceeds 80%
- Scales workers up when CPU has headroom and throughput is below expected
- Increases chunk size when chunks complete too fast, decreases when chunks time out
- All adjustments written to Redis immediately and logged to the immutable audit trail

---

## Technology Stack

| Layer | Technology | Purpose |
|---|---|---|
| Language | Python 3.11+ | Primary implementation language |
| Web Framework | FastAPI | REST API, async request handling, auto-generated OpenAPI docs |
| Metadata Database | PostgreSQL 14+ | ACID transactions, JSONB, partial indexes |
| Queue | Redis | BRPOP-based chunk queue, pub/sub Event Bus, throttle state |
| CDC (MySQL) | mysql-replication | Binlog streaming |
| CDC (PostgreSQL) | psycopg2 | WAL logical replication |
| File Processing | pandas, pyarrow | CSV, JSON, Parquet, Excel |
| Cloud Storage | boto3, azure-storage-blob, google-cloud-storage | S3, Azure Blob, GCS |
| Streaming | confluent-kafka | Kafka producer/consumer |
| Synthetic Data | faker | Deterministic fake data generation |
| Encryption | cryptography (Fernet) | Credential and PII encryption |
| Metrics | Prometheus + Grafana | 15 custom metrics, dashboards, alerts |
| Observability | structlog | Structured JSON logging |

---

## Database Schema

The `migration_metadata` PostgreSQL database contains 40+ tables organized into functional groups:

| Group | Tables | Purpose |
|---|---|---|
| Core | migration_jobs, migration_tables, migration_chunks | Job and execution state |
| Schema | schema_versions, schema_table_mappings, schema_column_mappings | Schema discovery and mappings |
| Workers | worker_heartbeats | Worker registration and health |
| Intelligence | metadata_catalog, intelligence_scan_jobs, assessment_reports, data_quality_results | Pre-migration analysis |
| Workflow | workflow_definitions, workflow_executions, workflow_node_log | Execution pipeline |
| CDC | cdc_sessions, cdc_events, cutover_log | Change data capture |
| Security | tenants, users, roles, api_keys, audit_logs, secrets_vault | Auth and governance |
| Operations | operations_actions, maintenance_mode, self_tuning_actions, benchmark_records | Runtime control |
| Masking | masking_rule_sets, masking_rules, masking_job_log | Data protection |
| Plugins | plugin_registry, event_log, event_subscriptions, service_registry | Kernel infrastructure |

---

## Getting Started

### Prerequisites

- Python 3.11+
- PostgreSQL 14+
- Redis 6+

### Installation

```bash
git clone <repository>
cd migration

# Create virtual environment
python -m venv venv
source venv/bin/activate      # Linux/macOS
.\venv\Scripts\Activate.ps1   # Windows

# Install dependencies
pip install -r requirements.txt
```

### Database Setup

```bash
# Create the metadata database
psql -U postgres -c "CREATE DATABASE migration_metadata;"

# Run migrations in order
psql -U postgres -d migration_metadata -f db_migrations/001_schema_mapping_tables.sql
psql -U postgres -d migration_metadata -f db_migrations/002_validation_engine.sql
psql -U postgres -d migration_metadata -f db_migrations/003_schema_versioning.sql
psql -U postgres -d migration_metadata -f db_migrations/004b_fix_existing_tables.sql
psql -U postgres -d migration_metadata -f db_migrations/004_security_saas.sql
psql -U postgres -d migration_metadata -f db_migrations/005_connectors_cdc.sql
psql -U postgres -d migration_metadata -f db_migrations/006_kernel_foundation.sql
psql -U postgres -d migration_metadata -f db_migrations/007_workflow_engine.sql
psql -U postgres -d migration_metadata -f db_migrations/008_metadata_intelligence.sql
psql -U postgres -d migration_metadata -f db_migrations/009_intelligence_service.sql
psql -U postgres -d migration_metadata -f db_migrations/010_simulation.sql
psql -U postgres -d migration_metadata -f db_migrations/011_live_intelligence.sql
psql -U postgres -d migration_metadata -f db_migrations/012_masking.sql
psql -U postgres -d migration_metadata -f db_migrations/013_operations_console.sql
```

### Environment Variables

```env
# Database
DATABASE_URL=postgresql://postgres:password@localhost:5432/migration_metadata

# Redis
REDIS_URL=redis://localhost:6379

# Security
JWT_SECRET=<generate-a-long-random-string>
JWT_EXPIRE_HOURS=24
MIGRATION_ENCRYPTION_KEY=<from: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())">

# Optional: CDC (MySQL)
# Requires binlog_format=ROW and log_bin=ON on MySQL server

# Optional: CDC (PostgreSQL)
# Requires wal_level=logical in postgresql.conf
```

### Starting Services

```bash
# Core services (start all or just what you need)
uvicorn backend.control_plane.app.main:app --port 8000 --reload
uvicorn backend.monitoring_service.app.main:app --port 8001 --reload
uvicorn backend.schema_mapping_service.app.main:app --port 8003 --reload
uvicorn backend.enterprise.security_main:app --port 8005 --reload
uvicorn backend.connector_framework.main:app --port 8006 --reload
uvicorn backend.kernel.main:app --port 8007 --reload
uvicorn backend.workflow_engine.main:app --port 8008 --reload
uvicorn backend.intelligence.main:app --port 8009 --reload
uvicorn backend.intelligence_service.main:app --port 8010 --reload
uvicorn backend.simulation.main:app --port 8011 --reload
uvicorn backend.live_intelligence.main:app --port 8012 --reload
uvicorn backend.masking.main:app --port 8013 --reload
uvicorn backend.plugins.main:app --port 8014 --reload
uvicorn backend.connectors.main:app --port 8015 --reload
uvicorn backend.operations.main:app --port 8016 --reload

# Start workers (run as many as needed)
WORKER_ID=worker-1 python -m backend.worker_service.app.worker
WORKER_ID=worker-2 python -m backend.worker_service.app.worker
WORKER_ID=worker-3 python -m backend.worker_service.app.worker
```

---

## Typical Migration Flow

```
1. Connect source and target databases
   POST /connections  (port 8000)

2. Discover and compare schemas
   POST /schemas/discover  (port 8003)

3. Run pre-migration intelligence scan
   POST /intelligence/scans  (port 8009)

4. Review assessment and advice
   POST /assess  (port 8010)
   POST /advise  (port 8010)

5. Scan for data quality issues
   POST /quality/scan-all  (port 8010)

6. Configure column mappings and run dry-run
   POST /projects/{id}/dry-run  (port 8003)

7. Run policy checks
   POST /plugins/policies/check/{job_id}  (port 8014)

8. Simulate with different worker counts
   POST /simulate/worker-sweep  (port 8011)

9. Create migration job and start workers
   POST /jobs  (port 8000)
   WORKER_ID=worker-{n} python -m backend.worker_service.app.worker

10. (Optional) Enable CDC for near-zero downtime
    POST /cdc/sessions  (port 8006)

11. Monitor live progress
    GET /ops/jobs/{id}/live-stats  (port 8016)

12. Post-migration: record benchmark
    POST /live/benchmark/record/{job_id}  (port 8012)
```

---

## Supported Connectors

### Databases
| Connector | Source | Target | CDC |
|---|---|---|---|
| MySQL 5.6–8.x | ✅ | ✅ | ✅ Binlog |
| PostgreSQL 11–16 | ✅ | ✅ | ✅ WAL |
| MariaDB 10.4+ | ✅ | ✅ | ✅ Binlog |
| SQLite | ✅ | ✅ | ❌ |

### Files and Storage
| Connector | Source | Target |
|---|---|---|
| CSV / TSV | ✅ | ✅ |
| Excel (.xlsx) | ✅ | ✅ |
| JSON (newline-delimited) | ✅ | ✅ |
| Parquet | ✅ | ✅ |
| Avro | ✅ | ✅ |
| AWS S3 | ✅ | ✅ |
| Azure Blob Storage | ✅ | ✅ |
| Google Cloud Storage | ✅ | ✅ |

### APIs and Streaming
| Connector | Source | Target |
|---|---|---|
| REST API (paginated) | ✅ | ✅ |
| Apache Kafka | ✅ | ✅ |

---

## Security

- **Authentication**: Self-issued JWT tokens with configurable expiry
- **Authorization**: 7-role RBAC (platform_admin, tenant_admin, migration_admin, migration_operator, read_only, auditor, api_client)
- **Credential Storage**: AES-128-CBC (Fernet) encryption for all database credentials
- **Audit Trail**: Append-only `audit_logs` table — every significant action is recorded with before/after state, operator identity, and timestamp
- **Data Protection**: 7 masking strategies and 20 synthetic data generators ensure production data never reaches non-production environments
- **Policy Engine**: 7 organizational governance policies (require approval, forbidden lossy conversions, require masking for PII, etc.)

---

## Project Status

| Component | Status |
|---|---|
| Platform Kernel (Plugin Manager, Event Bus, Service Registry, Metadata Catalog) | ✅ Complete |
| Workflow Engine | ✅ Complete |
| Metadata Intelligence Layer | ✅ Complete |
| Intelligence Service (Assessment, Advisor, Estimator, Scanner) | ✅ Complete |
| Simulation Engine | ✅ Complete |
| Live Intelligence (Drift Detection, Self-Tuning, Benchmarking) | ✅ Complete |
| Data Masking and Synthetic Data Engine | ✅ Complete |
| Plugin Refactor (Validators, Transformers, Notifiers, Policies) | ✅ Complete |
| Extended Connectors (File, Object Storage, REST API, Kafka) | ✅ Complete |
| Operations Console | ✅ Complete |
| Scheduler, Reporting, Knowledge Base | 🔄 In Progress |
| React Frontend | ⏳ Planned |
| Kubernetes / Cloud-Native Deployment | ⏳ Planned |
| AI Copilot, NoSQL Connectors, Marketplace | ⏳ Planned |

---

## Architectural Decisions

All major architectural decisions are documented in [`DECISIONS.md`](./DECISIONS.md), covering 38 decisions across infrastructure, architecture, schema mapping, connectors, CDC, operations, and planned future work. This document is the authoritative record of why the platform is designed the way it is.

Key decisions include:
- **Redis over Kafka** for the MVP queue (Kafka adds operational overhead not justified at current scale)
- **PostgreSQL over MongoDB** for metadata (ACID guarantees, JSONB, advanced indexes)
- **Chunk-based migration** for resume, retry, parallelism, and per-chunk validation
- **Workflow Engine as execution kernel** rather than a hardcoded pipeline in ChunkExecutor
- **Plugin registry pattern** rather than dependency injection frameworks
- **No Universal Data Model in MVP** — SQL-to-SQL scope, NoSQL deferred to the AI layer

---

## Contributing

All contributions should be preceded by an entry in `DECISIONS.md` for any architectural change. Bug fixes and feature additions to existing modules follow the standard pull request process.

When adding a new connector:
1. Subclass `DatabaseConnector` in `backend/connectors/{name}/{name}_connector.py`
2. Implement all `@abstractmethod` methods
3. Register: `ConnectorRegistry.register("name", MyConnector)`
4. Add capability flags to `ConnectorCapabilities`

When adding a new workflow node:
1. Subclass `WorkflowNode` in `backend/workflow_engine/nodes/`
2. Implement `execute(ctx: WorkflowContext) -> WorkflowContext`
3. Register: `PluginManager.register("workflow_node", "NodeName", MyNode)`

---

## License

Proprietary. All rights reserved.
