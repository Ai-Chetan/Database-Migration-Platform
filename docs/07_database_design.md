# Database Design Document

# Database Schema and Data Migration Platform

---

# 1. Introduction

## 1.1 Purpose

This document defines the database architecture and metadata storage design for the Database Schema and Data Migration Platform.

The metadata database is the central persistence layer of the platform and is responsible for:

* Migration state management
* Chunk tracking
* Retry tracking
* User and tenant management
* Execution monitoring
* Audit logging
* Validation storage
* Workflow coordination

The database design prioritizes:

* Reliability
* Scalability
* Consistency
* Fault tolerance
* Observability
* Maintainability

---

# 1.2 Database Selection

## Primary Metadata Database

The platform uses:

```text
PostgreSQL
```

as the primary metadata database.

---

## Reason for PostgreSQL Selection

PostgreSQL is selected because it provides:

* Strong ACID compliance
* Reliable transaction handling
* Advanced indexing support
* JSON support
* Partitioning capabilities
* High scalability
* Enterprise reliability
* Mature ecosystem
* Excellent concurrency handling

---

# 2. Database Architecture Overview

The platform maintains three logical database categories:

| Database Type     | Purpose                            |
| ----------------- | ---------------------------------- |
| Metadata Database | Internal platform state management |
| Source Databases  | Migration source systems           |
| Target Databases  | Migration destination systems      |

---

# 3. Metadata Database Design Goals

The metadata database must support:

* Persistent execution tracking
* Multi-tenant isolation
* Concurrent worker coordination
* Large-scale chunk tracking
* Retry and recovery management
* Monitoring and analytics
* Auditability
* Operational observability

---

# 4. Multi-Tenant Design

## Tenant Isolation Strategy

The platform initially uses:

```text
Shared Database + Tenant Isolation
```

Each record includes:

```text
tenant_id
```

for logical separation.

---

## Benefits

This model provides:

* Simpler infrastructure
* Lower operational cost
* Easier maintenance
* Easier scaling initially
* Simplified deployment

---

## Future Evolution

Future enterprise versions may support:

* Schema-per-tenant
* Database-per-tenant

for stronger isolation requirements.

---

# 5. Core Database Entities

The metadata database contains the following primary entities:

| Entity             | Purpose                        |
| ------------------ | ------------------------------ |
| tenants            | Organization management        |
| users              | User accounts                  |
| roles              | Role definitions               |
| migration_jobs     | Migration lifecycle tracking   |
| migration_tables   | Table-level migration state    |
| migration_chunks   | Chunk-level execution tracking |
| chunk_attempts     | Retry tracking                 |
| table_mappings     | Table relationship definitions |
| column_mappings    | Column transformation mappings |
| worker_heartbeats  | Worker monitoring              |
| validation_results | Validation tracking            |
| audit_logs         | Audit history                  |
| notifications      | User notifications             |

---

# 6. Entity Relationship Overview

```text
Tenant
  ├── Users
  ├── Migration Jobs
  │      ├── Migration Tables
  │      │      ├── Migration Chunks
  │      │      │      └── Chunk Attempts
  │      │
  │      ├── Table Mappings
  │      ├── Column Mappings
  │      ├── Validation Results
  │      └── Audit Logs
  │
  └── Notifications
```

---

# 7. Core Table Definitions

# 7.1 tenants

## Purpose

Stores organization-level information.

---

## Schema

```sql
CREATE TABLE tenants (
    id UUID PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    slug VARCHAR(255) UNIQUE NOT NULL,
    status VARCHAR(50) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);
```

---

# 7.2 users

## Purpose

Stores platform user accounts.

---

## Schema

```sql
CREATE TABLE users (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL REFERENCES tenants(id),
    email VARCHAR(255) UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    full_name VARCHAR(255),
    role VARCHAR(100) NOT NULL,
    status VARCHAR(50) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);
```

---

# 7.3 migration_jobs

## Purpose

Tracks complete migration workflows.

---

## Schema

```sql
CREATE TABLE migration_jobs (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL REFERENCES tenants(id),

    job_name VARCHAR(255) NOT NULL,
    description TEXT,

    source_db_type VARCHAR(100),
    target_db_type VARCHAR(100),

    source_connection_name VARCHAR(255),
    target_connection_name VARCHAR(255),

    status VARCHAR(50) NOT NULL,

    total_tables INTEGER DEFAULT 0,
    completed_tables INTEGER DEFAULT 0,

    total_chunks BIGINT DEFAULT 0,
    completed_chunks BIGINT DEFAULT 0,
    failed_chunks BIGINT DEFAULT 0,

    created_by UUID REFERENCES users(id),

    started_at TIMESTAMP,
    completed_at TIMESTAMP,

    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);
```

---

# 7.4 migration_tables

## Purpose

Tracks table-level migration state.

---

## Schema

```sql
CREATE TABLE migration_tables (
    id UUID PRIMARY KEY,

    job_id UUID NOT NULL REFERENCES migration_jobs(id),

    source_table_name VARCHAR(255),
    target_table_name VARCHAR(255),

    mapping_type VARCHAR(50),

    total_rows BIGINT DEFAULT 0,
    migrated_rows BIGINT DEFAULT 0,

    total_chunks BIGINT DEFAULT 0,
    completed_chunks BIGINT DEFAULT 0,

    status VARCHAR(50),

    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);
```

---

# 7.5 migration_chunks

## Purpose

Tracks chunk-level execution state.

This is one of the most critical tables in the system.

---

## Schema

```sql
CREATE TABLE migration_chunks (
    id UUID PRIMARY KEY,

    job_id UUID NOT NULL REFERENCES migration_jobs(id),
    table_id UUID NOT NULL REFERENCES migration_tables(id),

    chunk_number BIGINT NOT NULL,

    start_pk BIGINT,
    end_pk BIGINT,

    estimated_rows BIGINT,

    status VARCHAR(50) NOT NULL,

    assigned_worker_id UUID,

    retry_count INTEGER DEFAULT 0,
    max_retries INTEGER DEFAULT 5,

    started_at TIMESTAMP,
    completed_at TIMESTAMP,

    last_heartbeat TIMESTAMP,

    checksum TEXT,

    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);
```

---

# 7.6 chunk_attempts

## Purpose

Tracks individual chunk retry attempts.

---

## Schema

```sql
CREATE TABLE chunk_attempts (
    id UUID PRIMARY KEY,

    chunk_id UUID NOT NULL REFERENCES migration_chunks(id),

    worker_id UUID,

    attempt_number INTEGER NOT NULL,

    status VARCHAR(50),

    started_at TIMESTAMP,
    completed_at TIMESTAMP,

    error_message TEXT,
    stack_trace TEXT,

    rows_processed BIGINT DEFAULT 0,

    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
```

---

# 7.7 table_mappings

## Purpose

Stores table mapping configurations.

---

## Schema

```sql
CREATE TABLE table_mappings (
    id UUID PRIMARY KEY,

    job_id UUID NOT NULL REFERENCES migration_jobs(id),

    source_table VARCHAR(255),
    target_table VARCHAR(255),

    mapping_type VARCHAR(50),

    join_condition TEXT,

    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
```

---

# 7.8 column_mappings

## Purpose

Stores column-level mapping definitions.

---

## Schema

```sql
CREATE TABLE column_mappings (
    id UUID PRIMARY KEY,

    table_mapping_id UUID NOT NULL REFERENCES table_mappings(id),

    source_column VARCHAR(255),
    target_column VARCHAR(255),

    transformation_rule TEXT,

    is_nullable BOOLEAN DEFAULT TRUE,

    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
```

---

# 7.9 worker_heartbeats

## Purpose

Tracks worker health and activity.

---

## Schema

```sql
CREATE TABLE worker_heartbeats (
    id UUID PRIMARY KEY,

    worker_name VARCHAR(255),

    worker_status VARCHAR(50),

    current_chunk_id UUID,

    hostname VARCHAR(255),

    cpu_usage NUMERIC(5,2),
    memory_usage NUMERIC(5,2),

    last_heartbeat TIMESTAMP NOT NULL,

    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
```

---

# 7.10 validation_results

## Purpose

Stores migration validation results.

---

## Schema

```sql
CREATE TABLE validation_results (
    id UUID PRIMARY KEY,

    job_id UUID NOT NULL REFERENCES migration_jobs(id),

    table_id UUID REFERENCES migration_tables(id),

    validation_type VARCHAR(100),

    source_value TEXT,
    target_value TEXT,

    validation_status VARCHAR(50),

    validation_message TEXT,

    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
```

---

# 7.11 audit_logs

## Purpose

Stores operational audit records.

---

## Schema

```sql
CREATE TABLE audit_logs (
    id UUID PRIMARY KEY,

    tenant_id UUID NOT NULL REFERENCES tenants(id),

    user_id UUID REFERENCES users(id),

    action VARCHAR(255),

    entity_type VARCHAR(100),
    entity_id UUID,

    metadata JSONB,

    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
```

---

# 7.12 notifications

## Purpose

Stores user notification records.

---

## Schema

```sql
CREATE TABLE notifications (
    id UUID PRIMARY KEY,

    tenant_id UUID NOT NULL REFERENCES tenants(id),

    user_id UUID REFERENCES users(id),

    title VARCHAR(255),
    message TEXT,

    notification_type VARCHAR(100),

    is_read BOOLEAN DEFAULT FALSE,

    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
```

---

# 8. Job Lifecycle States

# 8.1 Migration Job States

The system supports the following job states:

| State     | Description                |
| --------- | -------------------------- |
| CREATED   | Job created                |
| PLANNING  | Chunk planning in progress |
| QUEUED    | Waiting for execution      |
| RUNNING   | Migration executing        |
| PAUSED    | Execution paused           |
| FAILED    | Migration failed           |
| COMPLETED | Migration successful       |
| CANCELLED | Job cancelled              |

---

# 8.2 Chunk States

| State     | Description            |
| --------- | ---------------------- |
| PENDING   | Waiting for execution  |
| ASSIGNED  | Assigned to worker     |
| RUNNING   | Currently executing    |
| FAILED    | Execution failed       |
| RETRYING  | Waiting for retry      |
| COMPLETED | Successfully completed |

---

# 9. Indexing Strategy

# 9.1 Required Indexes

## migration_jobs

```sql
CREATE INDEX idx_jobs_tenant_status
ON migration_jobs (tenant_id, status);
```

---

## migration_chunks

```sql
CREATE INDEX idx_chunks_status
ON migration_chunks (status);

CREATE INDEX idx_chunks_worker
ON migration_chunks (assigned_worker_id);

CREATE INDEX idx_chunks_job
ON migration_chunks (job_id);
```

---

## chunk_attempts

```sql
CREATE INDEX idx_attempts_chunk
ON chunk_attempts (chunk_id);
```

---

## audit_logs

```sql
CREATE INDEX idx_audit_tenant
ON audit_logs (tenant_id);

CREATE INDEX idx_audit_created
ON audit_logs (created_at);
```

---

# 10. Partitioning Strategy

# 10.1 Large Tables

The following tables are expected to grow rapidly:

* audit_logs
* chunk_attempts
* migration_chunks

---

# 10.2 Partition Strategy

Future production deployments should use:

* Time-based partitioning
* Job-based partitioning

to improve query performance.

---

# 11. Retry and Recovery Design

# 11.1 Retry Tracking

Retry management includes:

* retry_count
* max_retries
* retry status
* retry timestamps

---

# 11.2 Recovery Design

The database stores enough execution state to support:

* Resume after crash
* Worker reassignment
* Stale task recovery
* Partial execution continuation

---

# 12. Worker Coordination Design

# 12.1 Heartbeat Tracking

Workers periodically update heartbeat records.

---

# 12.2 Stale Worker Detection

Workers are considered stale if heartbeat timeout exceeds threshold.

---

# 12.3 Chunk Reassignment

Chunks owned by stale workers may be reassigned.

---

# 13. Validation Storage Design

Validation system stores:

* Row count validation
* Checksum validation
* Structural validation
* Error summaries

---

# 14. Security Design

# 14.1 Credential Storage

Sensitive credentials must be encrypted before storage.

---

# 14.2 Tenant Isolation

All operational records include tenant isolation fields.

---

# 14.3 Auditability

Critical operations are audit logged.

---

# 15. Scalability Considerations

The metadata database is designed to support:

* Millions of chunks
* Concurrent worker execution
* Concurrent migration jobs
* Large audit datasets

---

# 16. Future Database Enhancements

Future enhancements may include:

* Read replicas
* Multi-region replication
* Dedicated analytics database
* Event sourcing
* Time-series metrics storage

---

# 17. Conclusion

This database design establishes the foundational persistence architecture for the Database Schema and Data Migration Platform.

The metadata database is designed to support reliable migration execution, fault-tolerant recovery, scalability, observability, and enterprise-grade operational management while enabling future architectural evolution and distributed execution capabilities.
