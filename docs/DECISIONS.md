# DECISIONS.md
# Migration Platform Kernel — Architectural Decision Record

Every major decision made during design and implementation.
Format: Decision ID, Context, Alternatives Considered, Decision, Reason, Date, Status.

---

## INFRASTRUCTURE & STACK

---

### DECISION-001 — Language: Python

**Context:** Choose the primary backend language for the migration platform.

**Alternatives:**
- Spring Boot (Java) — mature enterprise ecosystem, verbose, slow iteration
- Go — excellent performance, weak ORM/DB ecosystem, harder to onboard contributors
- Node.js — async-first, but weak for CPU-bound data processing and scientific libraries

**Decision:** Python (FastAPI)

**Reasons:**
- Fastest iteration speed for a solo/small team building a complex platform
- Rich ecosystem: SQLAlchemy, pandas, pyarrow, faker, cryptography, psycopg2, mysql-connector all first-class
- async/await support via FastAPI for high-concurrency HTTP without threading complexity
- Existing data engineering tooling (Airflow DAG generation, Spark scripts) is Python-native
- Type hints + Pydantic give compile-time-like safety without Java verbosity

**Status:** Frozen (MVP)

---

### DECISION-002 — Web Framework: FastAPI

**Context:** Choose the HTTP framework for all microservices.

**Alternatives:**
- Django REST Framework — too heavy, admin panel unnecessary, ORM coupling
- Flask — too minimal, no async, no built-in validation, no OpenAPI auto-generation
- Tornado — low-level, poor DX
- Starlette — FastAPI is built on Starlette; using FastAPI is using Starlette with batteries

**Decision:** FastAPI

**Reasons:**
- Auto-generates OpenAPI/Swagger docs for every endpoint — critical for a platform with 60+ endpoints
- Pydantic request/response validation out of the box
- Native async support for non-blocking I/O (DB queries, Redis, external HTTP calls)
- Fastest Python web framework in benchmarks (comparable to Go/Node for I/O-bound work)
- Dependency injection system (`Depends`) keeps routers thin and testable

**Status:** Frozen (MVP)

---

### DECISION-003 — Metadata Database: PostgreSQL

**Context:** Choose the database for storing migration metadata (jobs, chunks, mappings, audit, etc.).

**Alternatives:**
- MySQL — solid but weaker JSONB support, less advanced index types
- SQLite — not suitable for concurrent multi-worker writes
- MongoDB — schemaless is a liability for metadata that has a known schema
- CockroachDB — operational complexity not justified at MVP scale

**Decision:** PostgreSQL

**Reasons:**
- ACID transactions — metadata integrity is non-negotiable when tracking chunk progress
- JSONB columns with indexing — used in schema_data, mapping_config, payload, capabilities without separate schema tables for each variant
- Advanced index types (partial indexes, expression indexes) — used in audit_logs, cdc_events
- `gen_random_uuid()` built-in — all PKs are UUIDs with zero application-level generation required
- `DISTINCT ON` — used in Metadata Catalog's "latest entry per type" query
- `text[]` / `ANY()` — used in Event Bus replay queries
- pg_relation_size, reltuples — used in schema discovery without extra tooling

**Status:** Frozen (MVP)

---

### DECISION-004 — Queue: Redis (not Kafka)

**Context:** Choose the message queue for chunk work distribution to workers.

**Alternatives:**
- Kafka — overkill for MVP; operational complexity (ZooKeeper/KRaft, topic management, consumer groups) far exceeds the benefit at current scale
- RabbitMQ — good choice but adds another service; Redis already required for caching/throttle state
- SQS (AWS) — cloud-lock, not available for on-premise deployments
- PostgreSQL LISTEN/NOTIFY — fragile for high-throughput worker queues, not designed for this

**Decision:** Redis (BRPOP-based queue)

**Reasons:**
- Already required in the stack for: throttle state (`migration:throttle:{job_id}`), CDC session coordination, Event Bus pub/sub, session caching
- Zero additional operational overhead — one Redis instance serves 5 different purposes
- BRPOP gives blocking pop with a timeout — workers sleep efficiently without polling
- LPUSH/RPUSH gives natural FIFO queue semantics with priority lane via separate list keys
- List atomicity — exactly-once pop even under high concurrency without distributed locks

**V2 Note:** Event Bus should eventually abstract the backend (Redis/Kafka/RabbitMQ/NATS) via an interface, so Kafka can be swapped in for high-throughput CDC event streaming without changing subscriber code. Frozen for MVP.

**Status:** Frozen (MVP)

---

### DECISION-005 — Migration Strategy: Chunk-Based

**Context:** Choose how data is physically moved from source to target.

**Alternatives:**
- Full table dump — simple, but fails on large tables (memory, timeout, no resume)
- Row-by-row streaming — safe, but too slow for billions of rows
- Native DB dump/restore (mysqldump, pg_dump) — fast but bypasses all transformation/validation logic; unusable for cross-engine migrations

**Decision:** PK-range-based chunk migration

**Reasons:**
- **Resume:** a failed chunk can restart from its exact PK range without re-reading already-migrated rows
- **Retry:** per-chunk retry with exponential backoff; one bad row doesn't fail the whole table
- **Parallelism:** multiple workers independently own non-overlapping PK ranges; linear throughput scaling
- **Monitoring:** chunk-level progress tracking (N of M chunks done = exact %) is far more accurate than "table is 0% or 100%"
- **Validation:** checksums and row counts per chunk — much more precise than per-table
- **Memory safety:** fixed batch size (BATCH_SIZE = 5000 rows) means worker memory usage is bounded regardless of table size

**Status:** Frozen (MVP)

---

### DECISION-006 — Chunk Size: Adaptive (not fixed 100k)

**Context:** Original implementation used fixed chunk_size=100000 for all tables.

**Alternatives:**
- Fixed size — simple but wrong: a 250-row `country` table doesn't need 100k-row chunks; a 4B-row `transactions` table needs smaller chunks to avoid timeouts

**Decision:** AdaptiveChunkPlanner per table

**Reasons:**
- Analyzes: row count, avg row size (bytes), PK distribution (sequential/sparse/UUID), estimated execution time
- Strategies: full_table (<1k rows), size_based (target 32MB/chunk), count_based (wide rows >4KB), streaming (>500M rows), uuid_sparse (UUID/sparse PKs use offset-based)
- Results stored in `adaptive_chunk_configs` table and written back to `migration_tables.computed_chunk_size`
- Eliminates the worst failure modes: UUID-PK tables with range-based chunking (gaps cause empty chunks), wide-row tables with 100k chunks (OOM on workers)

**Status:** Frozen (MVP)

---

### DECISION-007 — ORM: SQLAlchemy (raw text() for new tables)

**Context:** Choose how to interact with the PostgreSQL metadata database.

**Alternatives:**
- Pure psycopg2 — too low level, no connection pooling management
- Django ORM — tightly coupled to Django's app model, not usable standalone
- Tortoise ORM — async-native but immature ecosystem

**Decision:** SQLAlchemy for connection management and session lifecycle; `text()` raw SQL for new tables not yet in the ORM model

**Reasons:**
- Existing models (MigrationJob, MigrationChunk, MigrationTable, WorkerHeartbeat) already defined as SQLAlchemy ORM models — consistent
- `get_db()` dependency injection via FastAPI's `Depends()` gives clean session lifecycle per request
- `text()` for new tables (schema_mapping_service tables, kernel tables, CDC tables, etc.) avoids the overhead of defining ORM models for 30+ new tables while keeping them in the same connection pool
- This is intentional technical debt: ORM models can be added later if needed; `text()` SQL is readable and explicit

**Status:** Frozen (MVP). ORM model generation for new tables is a V2 quality-of-life improvement, not a correctness issue.

---

## ARCHITECTURE

---

### DECISION-008 — Architecture: Microservices (not monolith)

**Context:** Choose the overall application architecture.

**Alternatives:**
- Monolith — simpler initially, but migration workloads have radically different scaling profiles: workers need horizontal scale, the UI needs low latency, CDC needs long-lived connections. A monolith can't serve these simultaneously without internal complexity that exceeds microservice boundary cost.
- Serverless — cold starts incompatible with long-running migration jobs and persistent CDC streams

**Decision:** Microservices on a shared PostgreSQL metadata database

**Reasons:**
- Workers scale independently of the control plane — the most important scaling axis for a migration platform is "how many workers can I throw at this"
- Each service owns exactly one concern; no service can break another service's correctness guarantees by sharing in-process state
- Shared PostgreSQL — services coordinate through the DB (jobs, chunks, heartbeats) rather than direct RPC — reduces inter-service coupling; any service can fail/restart without cascading

**Services and ports:**
```
8000 — Control Plane       (job management, planning, chunk generation)
8001 — Monitoring Service   (observability endpoints)
8003 — Schema Mapping Service (schema discovery, comparison, mappings, dry-run)
8004 — Enterprise Execution Engine (adaptive chunks, dependency graph, rollback)
8005 — Enterprise Security + SaaS (auth, RBAC, tenants, approvals, templates)
8006 — Connector Framework + CDC (connectors, CDC engine, policy engine)
8007 — Migration Platform Kernel (plugin manager, event bus, service registry, metadata catalog)
```

**Status:** Frozen (MVP)

---

### DECISION-009 — Plugin Architecture: Registry Pattern (not dependency injection framework)

**Context:** Choose how to make the platform extensible with new connectors, validators, transformers, etc.

**Alternatives:**
- Dependency injection container (e.g. Python's `injector`) — adds a framework dependency; overkill for a registry of class types
- Entry points (Python packaging) — correct for third-party plugins installed via pip; premature for MVP built-in plugins
- Abstract base classes with subclass scanning — too implicit; registration is magical and hard to debug

**Decision:** Explicit `PluginManager.register(type, name, class)` + `PluginManager.get(type, name, config)`

**Reasons:**
- Explicit registration at service startup is readable and debuggable — you can see exactly what's registered by reading `startup()` or the auto-registration method
- Generalizes the already-proven `ConnectorRegistry` pattern that was working before Part 1
- Dual-layer: in-memory dict (fast, per-process) + `plugin_registry` DB table (persistent, cross-process, introspectable via API)
- `sync_to_catalog()` bridges the two: built-ins register in memory at startup, then sync to DB once, so the Frontend/Marketplace can see them without querying every microservice's process

**V2 Note:** Metadata-driven installable plugins (VS Code extension model: id/version/author/signature/dependencies/health_status/config_schema) with static scan → signature verification → sandbox → install pipeline. This requires the Marketplace to exist first (Part 12+). Frozen for MVP.

**Status:** Frozen (MVP)

---

### DECISION-010 — Event System: Redis Pub/Sub + Durable Log (not direct HTTP calls)

**Context:** Services need to react to each other's events (job failed → notify, drift detected → pause, chunk completed → update progress).

**Alternatives:**
- Direct HTTP calls between services — N×N coupling; every service needs to know every other service's URL and schema
- Polling — wastes resources; latency depends on poll interval
- WebSockets — appropriate for client→server streaming (Frontend), not service→service

**Decision:** EventBus (Redis Pub/Sub for real-time delivery + `event_log` table for durability)

**Reasons:**
- Publisher never knows who's listening — adding a new subscriber (e.g. Knowledge Base in Part 9) requires zero changes to the publisher (Worker Engine)
- Redis Pub/Sub is already in the stack; zero new infrastructure
- `event_log` table gives durability: subscribers that were offline can call `replay_since()` to catch up — critical for Knowledge Base which may process events in batch, not real-time
- `get_timeline(correlation_id=job_id)` gives Operations Console its "everything that happened to this job" view for free — all events are already stored with correlation_id

**V2 Note:** EventBus should eventually abstract the backend via an interface (Redis/Kafka/RabbitMQ/NATS swappable). Frozen for MVP — Redis is sufficient.

**Status:** Frozen (MVP)

---

### DECISION-011 — Service Discovery: ServiceRegistry (simple DB table)

**Context:** Services need to find each other's base URLs without hardcoding `http://localhost:8003`.

**Alternatives:**
- Hardcoded URLs in config — works on a single machine, fails the moment anything moves
- Consul — correct production choice but adds operational dependency
- Kubernetes Service DNS — correct for K8s deployment but non-functional in local dev without K8s

**Decision:** `service_registry` PostgreSQL table + `ServiceRegistry.get_url(db, "schema_mapping_service")`

**Reasons:**
- Zero new infrastructure — uses existing PostgreSQL connection
- Background health checker updates status every 30s
- Seeded with all 7 known services at migration time — works out of the box
- Trivially replaceable in V2: `ServiceRegistry._backend` abstraction → plug in Consul or K8s DNS

**V2 Note:** Abstract over Local/Consul/Kubernetes/Eureka. Frozen for MVP.

**Status:** Frozen (MVP)

---

### DECISION-012 — Metadata Storage: Single Table with catalog_type Discriminator

**Context:** The Metadata Intelligence Layer (Part 3) needs to store table-level statistics, relationships, distributions, LOB detection results, etc.

**Alternatives:**
- Separate table per catalog type (`table_statistics`, `table_relationships`, `table_distributions`...) — rigid schema; adding a new catalog type requires a migration
- Document store (MongoDB) — introduces a new service dependency just for metadata

**Decision:** Single `metadata_catalog` table with `catalog_type` VARCHAR discriminator + `data` JSONB

**Reasons:**
- Adding a new catalog type (e.g. `sharding_detection` in V2) requires zero schema change — just write with a new `catalog_type` string
- `DISTINCT ON (catalog_type)` query efficiently returns "latest of each type" without multiple queries
- JSONB `data` field lets each type have its own shape without pre-defining every field
- History is preserved naturally — each write is a new row; `get_history()` gives trend data for free

**V2 Note:** Split into 4 formal stores: Metadata, Statistics, Lineage, History — with proper schemas and retention policies. For MVP the discriminator column provides the same logical separation without structural complexity.

**Status:** Frozen (MVP)

---

### DECISION-013 — Workflow Engine: Node-Based DAG (replaces ChunkExecutor)

**Context:** The original `ChunkExecutor.execute()` was a 200-line monolith that hardcoded: read → apply mapping → write → validate → metrics. Adding any new step required modifying this class.

**Decision:** WorkflowEngine with a DAG of typed WorkflowNodes

**Reasons:**
- Each node (ReadNode, TransformNode, ValidateNode, WriteNode, VerifyNode, NotifyNode, MetricsNode, AuditNode) is a separate class implementing `execute(context) → context`
- Adding a new step (e.g. DataMaskingNode in Part 8) = add one class, register it, add it to the default workflow definition. Zero changes to existing nodes.
- Every customer can define custom workflows — this is the primary commercial differentiator over AWS DMS, which has a fixed pipeline
- WorkflowNode declares: inputs, outputs, retry_policy, timeout — self-describing pipelines enable the Simulation Engine to reason about them
- `WorkflowDefinition` is serializable JSON — stored, versioned, shareable as Templates

**MVP nodes (default workflow):**
```
ReadNode → TransformNode → ValidateNode → WriteNode → VerifyNode → NotifyNode → MetricsNode → AuditNode
```

**V2 Note:** Add formal rollback handlers (compensation actions) and saga pattern to each node. Frozen for MVP.

**Status:** Frozen (MVP)

---

### DECISION-014 — CDC Approach: Binlog (MySQL) + WAL (PostgreSQL)

**Context:** CDC (Change Data Capture) is needed for near-zero-downtime migrations.

**Alternatives:**
- Timestamp-based CDC — only captures updated_at-tracked rows; misses DELETEs entirely
- Trigger-based CDC — works on any DB but imposes 2-3x write overhead on source in production
- Query-based CDC — polling; misses rapid changes between polls; high DB load

**Decision:** Native replication protocols: binlog for MySQL, WAL logical replication for PostgreSQL

**Reasons:**
- Zero overhead on source DB writes — binlog/WAL is written regardless of CDC; CDC just reads it
- Captures INSERT/UPDATE/DELETE exhaustively — nothing missed
- Ordered — events arrive in commit order, ensuring replay is deterministic
- `mysql-replication` library for binlog; `psycopg2` LogicalReplicationConnection for WAL — both well-maintained

**Requirements:**
- MySQL: `binlog_format=ROW`, `log_bin=ON`
- PostgreSQL: `wal_level=logical`

**Status:** Frozen (MVP)

---

### DECISION-015 — Security: JWT + RBAC (not session cookies)

**Context:** API authentication for a multi-tenant SaaS platform.

**Alternatives:**
- Session cookies — not suitable for API-first design where clients may be Python scripts, CLI tools, or third-party integrations
- OAuth2 provider (Auth0, Okta) — cloud dependency; adds cost and latency; unnecessary for MVP

**Decision:** Self-issued JWT tokens + 7-role RBAC

**Roles:**
```
platform_admin    → ["*"]
tenant_admin      → ["jobs:*","connections:*","users:*","mappings:*","settings:*"]
migration_admin   → ["jobs:*","connections:read","mappings:*","schemas:*"]
migration_operator→ ["jobs:read","jobs:start","jobs:pause","jobs:monitor","connections:read"]
read_only         → ["jobs:read","connections:read","mappings:read","schemas:read"]
auditor           → ["jobs:read","connections:read","audit:*","reports:*"]
api_client        → ["jobs:*","connections:read","mappings:read"]
```

**Reasons:**
- JWT is stateless — workers and services can verify tokens without a central session store
- 7 roles covers the full enterprise governance spectrum (developer, DBA, manager, auditor, API integration)
- Permission format `resource:action` is granular enough for enterprise, simple enough to implement in one `user.can()` method

**Status:** Frozen (MVP)

---

### DECISION-016 — Password/Credential Encryption: Fernet (AES-128-CBC + HMAC)

**Context:** Database credentials must never be stored in plaintext in PostgreSQL.

**Alternatives:**
- bcrypt — one-way hash, not reversible; cannot be decrypted to establish DB connections
- RSA asymmetric — overkill; key management complexity not justified at MVP scale
- Vault/AWS Secrets Manager — correct V2 choice; adds external dependency

**Decision:** Fernet (from Python `cryptography` library) with key from `MIGRATION_ENCRYPTION_KEY` env var

**Reasons:**
- AES-128-CBC + HMAC-SHA256 — industry standard, not homebrew
- Reversible — credentials must be decrypted to actually connect to databases
- Key in env var — never in code, never in DB
- Fallback to base64 with warning if `cryptography` not installed — graceful degradation for dev environments

**V2 Note:** Integrate Vault/AWS Secrets Manager/Azure Key Vault/GCP Secret Manager as an alternative key provider. The encryption/decryption API (`encrypt_password()` / `decrypt_password()`) is already a single-function interface — plugging in Vault means replacing those two functions only.

**Status:** Frozen (MVP)

---

### DECISION-017 — Multi-Tenancy: Shared Database, Tenant ID Column

**Context:** Choose isolation model for a SaaS platform.

**Alternatives:**
- Database-per-tenant — strongest isolation, highest operational cost; unmanageable at scale
- Schema-per-tenant (PostgreSQL) — moderate isolation, complex migrations
- Row-level security (PostgreSQL RLS) — elegant but complex to implement correctly under ORMs

**Decision:** Shared database, `tenant_id` column on all tenant-scoped tables, enforced in application layer

**Reasons:**
- Simplest to implement correctly — every query filters by `tenant_id` where tenant context is known
- Easiest to operate — one DB, one backup, one migration job affects all tenants consistently
- Adequate for MVP scale — the risk of data leakage is mitigated by `get_current_user()` always injecting `user.tenant_id` into queries, never trusting client-supplied tenant_id for authorization

**V2 Note:** Add PostgreSQL RLS as an additional defense-in-depth layer.

**Status:** Frozen (MVP)

---

### DECISION-018 — Audit Trail: Append-Only `audit_logs` Table

**Context:** Enterprise customers require immutable records of every significant action.

**Decision:** Append-only `audit_logs` table. Never UPDATE or DELETE rows.

**Reasons:**
- Immutability is the audit requirement — if rows can be deleted, the audit is meaningless
- `AuditTrail.log()` never raises — audit failures are logged but never propagate to the caller, ensuring a failed audit write never breaks the actual operation
- Covers: who (user_id, email), what (action string), which resource (resource_type + resource_id), before/after state (old_value/new_value JSONB), when (created_at), where from (ip_address, user_agent), result (success/failed/denied)

**Status:** Frozen (MVP)

---

## SCHEMA MAPPING

---

### DECISION-019 — Schema Comparison: Levenshtein Similarity for Rename Detection

**Context:** When comparing source and target schemas, a column renamed from `cust_name` to `customer_name` should be detected as a rename, not a drop + add.

**Decision:** Levenshtein similarity ≥ 0.65 threshold flags a column as a rename candidate

**Reasons:**
- Exact string comparison would treat every rename as drop+add — incorrect and would cause data loss in any migration that renames columns
- 0.65 threshold chosen empirically: catches real abbreviation→expansion renames while avoiding false positives between unrelated column names
- Recommendation Engine uses the same similarity function but with a 0.60 threshold (more permissive, since recommendations can be rejected by the user; the comparator is more conservative since its output drives diff display)

**Status:** Frozen (MVP)

---

### DECISION-020 — Column Mapping Kinds: 6 Types

**Context:** Define the finite set of ways a source column can map to a target column.

**Decision:** direct | rename | transform | constant | expression | lookup

**Reasoning:**
- `direct` — copy as-is; covers 80% of cases
- `rename` — copy from differently-named source column; next most common
- `transform` — apply Python expression to single column value; handles type coercions and simple derivations
- `constant` — always write a fixed literal; used for new required columns in target that have no source equivalent
- `expression` — compute from multiple source columns (`row['first'] + ' ' + row['last']`); needed for column consolidation
- `lookup` — replace value with a lookup from another table; needed for denormalization and code table expansion

**V2 Note:** Two new mapping_kind values added in Part 8: `mask` (hash/redact/partial/encrypt) and `synthesize` (faker, deterministically seeded).

**Status:** Frozen (MVP)

---

### DECISION-021 — Type Conversion Rules: DB-Backed (not hardcoded)

**Context:** Whether a type conversion is safe/lossy/unsafe needs to be configurable per tenant, not hardcoded in Python if/elif chains.

**Decision:** `datatype_conversion_rules` table with (tenant_id, source_db, target_db, source_type, target_type) unique key + safety/cast_template columns

**Reasons:**
- Tenant overrides: a tenant can mark `text→varchar` as safe for their specific workload where they know text values are always short
- Cross-engine awareness: MySQL `datetime` → PostgreSQL `timestamp` has different safety than MySQL `datetime` → MySQL `datetime`
- Seeded with 50+ built-in rules at migration time; new rules added by INSERT, not code change
- In-memory fallback when DB unavailable (uses the `conversion_safety()` function from `schema_comparator.py`)

**Status:** Frozen (MVP)

---

## CONNECTORS & CDC

---

### DECISION-022 — Connector Interface: Abstract Base Class

**Context:** How to ensure MySQL, PostgreSQL, SQLite, and future connectors (File, S3, API, Kafka, MongoDB) all implement the same contract.

**Decision:** `DatabaseConnector` abstract base class with `@abstractmethod` decorators

**Abstract methods (must implement):**
```python
connect(), disconnect(), test_connection()
discover_schema() → SchemaInfo
get_row_count(table) → int
get_avg_row_size(table) → int
stream_rows(table, pk_col, pk_start, pk_end) → Generator
bulk_insert(table, rows, mode) → BulkWriteResult
count_rows_in_range(table, pk_col, start, end) → int
compute_checksum(table, pk_col, start, end) → str
```

**Optional overrides (raise NotImplementedError if not supported):**
```python
generate_create_table_ddl(), execute_ddl(), truncate_table()
get_cdc_position(), start_cdc_capture(), stop_cdc_capture()
```

**Capability flags:** `ConnectorCapabilities(discover, stream_read, bulk_write, cdc, checksum, constraints, indexes, jsonb, partitioning)` — callers check capabilities before calling optional methods

**Status:** Frozen (MVP)

---

### DECISION-023 — No UDM (Universal Data Model) in MVP

**Context:** Supporting SQL→NoSQL (e.g. MySQL→MongoDB) requires a paradigm-neutral abstraction layer between the source schema and the target schema, since SQL tables and MongoDB documents have fundamentally different structures.

**Decision:** Deferred to Part 15 (AI/NoSQL part)

**Reasons:**
- MVP is SQL-only. Adding UDM now means every SQL connector, mapper, transformer, and workflow node must handle a new abstraction, all before any non-SQL connector exists to justify it.
- The existing `DatabaseConnector` interface and `SchemaInfo` structure are correct for SQL — no redesign needed for SQL-to-SQL migrations
- When Part 15 adds MongoDB/Neo4j/DynamoDB, UDM becomes the translation contract, with MySQL/PostgreSQL connectors gaining `to_udm()`/`from_udm()` as additive methods — no breaking changes

**Status:** Deferred to Part 15

---

### DECISION-024 — CDC Position Tracking: Method-Specific (binlog file+pos / WAL LSN)

**Context:** CDC replay requires knowing exactly where in the change stream to start, and where to resume after failure.

**Decision:** `CDCPosition` dataclass with method-discriminated fields (file+position for MySQL binlog, lsn for PostgreSQL WAL)

**Reasons:**
- MySQL binlog position is a (filename, integer offset) pair — cannot be expressed as a single integer
- PostgreSQL WAL LSN is a string like "0/3000290" — different format entirely
- Unified `CDCPosition` with all fields optional (None for inapplicable ones) keeps the capture/replay/cutover code engine-agnostic while storing the right metadata per engine

**Status:** Frozen (MVP)

---

## OPERATIONS

---

### DECISION-025 — Worker State Machine: IDLE / BUSY / STOPPING / OFFLINE

**Context:** Workers need a lifecycle that lets the control plane know whether a worker is available, working, gracefully shutting down, or dead.

**Decision:** Four states: IDLE → BUSY → STOPPING → OFFLINE

**Transitions:**
- IDLE: worker is running, waiting for work
- BUSY: worker has claimed a chunk and is executing
- STOPPING: graceful shutdown requested (finishes current chunk, doesn't pull new ones)
- OFFLINE: heartbeat missed for >N minutes (control plane infers this; worker doesn't set it)

**Reason:** STOPPING prevents job loss during deployments — in-flight chunk completes cleanly rather than being abandoned and retried.

**Status:** Frozen (MVP)

---

### DECISION-026 — Rollback Strategy: Per-Table (TRUNCATE vs DELETE_RANGE)

**Context:** If a migration fails partway through, the target database must be restored to its pre-migration state.

**Decision:** Per-table strategy based on pre-migration table state:
- Target table was EMPTY before migration → `TRUNCATE` (fast, removes all rows)
- Target table had PRE-EXISTING DATA → `DELETE` only rows in migrated PK ranges (preserves original data)

**Reasons:**
- Blanket TRUNCATE is wrong if the target table had pre-existing data the customer cares about
- DELETE by PK range is safe and idempotent — uses the same `migration_chunks.pk_start/pk_end` data already recorded during migration
- Rollback order is REVERSE of migration order to satisfy FK constraints (migrate country→orders, rollback orders→country)

**Status:** Frozen (MVP)

---

### DECISION-027 — Resource Throttling: Redis Key TTL (not a distributed lock)

**Context:** The Resource Governor needs to signal workers to slow down when source/target DB is under pressure.

**Decision:** `redis.setex("migration:throttle:{job_id}", TTL, allowed_worker_count)` — workers check this key before pulling new chunks

**Reasons:**
- Self-healing: key expires automatically if the governor process crashes — workers resume full speed without manual intervention
- No coordination needed: workers independently check the key on their own schedule
- Cheap: one Redis GET per chunk pull — negligible overhead
- TTL = 3 × check_interval: if the governor stops writing, the throttle auto-releases in 90 seconds

**Status:** Frozen (MVP)

---

### DECISION-028 — Dependency Graph Algorithm: Kahn's Topological Sort

**Context:** Tables with FK dependencies must be migrated in the correct order (referenced tables before referencing tables).

**Decision:** Kahn's algorithm (BFS-based topological sort with level tracking)

**Reasons:**
- Produces level-by-level output naturally (level 0 = no deps, level 1 = deps on level 0, etc.)
- Tables at the same level can migrate in PARALLEL — this is a direct performance benefit, not just correctness
- Detects circular FK dependencies cleanly (any table not visited after the main loop has a cycle)
- Circular FK tables are placed in a final level with a warning — they still migrate, just last

**Status:** Frozen (MVP)

---

## FRONTEND (PLANNED)

---

### DECISION-029 — Frontend State Management: TanStack Query + Zustand (not Redux)

**Context:** Choose React state management for the migration platform UI (Part 14).

**Decision:** TanStack Query for server state + Zustand for client/UI state

**Reasons:**
- TanStack Query handles the primary data need: server-synced data with caching, background refresh, optimistic updates — the Job Monitor's live progress bars, chunk grids, worker status are all server state
- Zustand for lightweight client state (current selected job, sidebar collapsed, filter values) — zero boilerplate vs Redux's action/reducer/selector ceremony
- Redux would be reasonable but its overhead is unjustified when TanStack Query already handles the hard part (server state) and the remaining client state is simple enough for Zustand's direct `set()` API

**Status:** Decided, implementation pending (Part 14)

---

## VERSION 2+ DECISIONS (Documented, Not Implemented)

---

### DECISION-030 — V2: Plugin Metadata-Driven Install (VS Code Extension Model)

**Context:** Long-term, plugins should be installable from a Marketplace without redeploying the platform.

**Decision:** Deferred to V2 / Part 12+

**Future design:** Each plugin carries: id, name, version, author, capabilities, supported_sources, supported_targets, min_platform_version, license, signature, dependencies, health_status, configuration_schema. Install pipeline: static scan → signature verification → sandbox test → activate.

**Frozen because:** No Marketplace exists yet. The current `PluginManager.register(type, name, class)` is the correct MVP interface — it can be extended to accept a metadata dict without breaking callers.

---

### DECISION-031 — V2: EventBus Backend Abstraction

**Context:** Long-term, customers with high-throughput CDC streaming may need Kafka instead of Redis Pub/Sub.

**Decision:** Deferred to V2

**Future design:** `EventBusBackend` abstract interface → `RedisEventBusBackend` (current), `KafkaEventBusBackend`, `RabbitMQEventBusBackend`, `NATSEventBusBackend`. `EventBus.publish()` delegates to the configured backend.

**Frozen because:** Redis Pub/Sub + `event_log` table is correct for MVP scale. Kafka adds operational complexity (KRaft/ZooKeeper, topic management, consumer group coordination) that requires a dedicated ops team to run reliably.

---

### DECISION-032 — V2: ServiceRegistry Abstraction (Consul/Kubernetes)

**Decision:** Deferred to V2. Current PostgreSQL-table implementation is correct for local dev and single-server deployments.

**Future design:** `ServiceRegistryBackend` interface → `LocalDbBackend` (current), `ConsulBackend`, `KubernetesBackend`, `EurekaBackend`.

---

### DECISION-033 — V2: Metadata Catalog Split into 4 Stores

**Decision:** Deferred to V2.

**Future design:** Split `metadata_catalog` table into: `catalog_metadata` (schema-level facts), `catalog_statistics` (numeric time-series: row counts, sizes, growth), `catalog_lineage` (source→transformation→target column tracking), `catalog_history` (point-in-time snapshots for time-travel). The current single-table + `catalog_type` discriminator is logically equivalent and trivially migrated when needed.

---

### DECISION-034 — V2: WorkflowNode Saga Pattern (Compensation Actions)

**Decision:** Deferred to V2. MVP nodes have retry_policy and timeout; compensation actions (formal rollback handlers per node) require the saga coordinator infrastructure.

---

### DECISION-035 — V2: Distributed Lock Manager

**Decision:** Redis single-instance locking is sufficient for MVP. V2: Redis Redlock (multi-instance) or etcd or ZooKeeper for coordinating workers across multiple Redis nodes in a Redis Cluster deployment.

---

### DECISION-036 — V2: Multi-Region Support

**Decision:** Single-region PostgreSQL + Redis. V2: US/EU/Asia clusters with one global control plane, per-region worker pools, global `service_registry` federation.

---

### DECISION-037 — V2: Secrets Manager External Integration

**Decision:** Current Fernet encryption with `MIGRATION_ENCRYPTION_KEY` env var is the MVP credential storage. V2: Plug in HashiCorp Vault / AWS Secrets Manager / Azure Key Vault / GCP Secret Manager behind the existing `encrypt_password()` / `decrypt_password()` two-function API — zero callers change.

---

### DECISION-038 — V2: API Gateway as Sole Ingress

**Decision:** Services exposed individually (ports 8000-8007) for MVP. V2: Kong or Traefik API Gateway as the single ingress with Auth → Rate Limit → Routing → Microservices. The `service_registry` table is already the routing table this gateway would read from.

---

*Last updated: Project session — Parts 1 through 1 (Kernel Foundation) complete. Parts 2-15 in progress.*
*Maintainer: Update this file whenever a significant architectural decision is made or changed.*
*Decision numbering: sequential, never reuse a number even if a decision is reversed — add a new decision referencing the old one.*
