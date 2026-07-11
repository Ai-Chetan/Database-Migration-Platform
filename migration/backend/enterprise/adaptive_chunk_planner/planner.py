"""
Adaptive Chunk Planner
File: migration/backend/enterprise/adaptive_chunk_planner/planner.py

Replaces the fixed chunk_size=100000 with intelligent per-table sizing.

Strategy selection:
  - FULL_TABLE   → < 1,000 rows         → migrate in one chunk
  - SIZE_BASED   → normal tables        → target 32MB per chunk
  - COUNT_BASED  → wide rows            → target 5,000 rows per chunk
  - STREAMING    → > 500M rows          → 10,000 rows, max parallelism
  - UUID_SPARSE  → UUID PKs / sparse    → adjust for gaps in PK range

Factors analyzed:
  - Total row count
  - Average row size (sampled from DB)
  - PK distribution (sequential vs sparse vs UUID)
  - Estimated execution time per chunk at known throughput benchmarks
  - Available memory headroom (worker batch_size_bytes target = 32MB)

Usage:
    planner = AdaptiveChunkPlanner()
    plan    = planner.compute(db, job_id, table_name, source_config)

    # plan.computed_chunk_size  → e.g. 25000
    # plan.strategy_used        → "size_based"
    # plan.computed_chunk_count → e.g. 160
    # plan.estimated_duration_sec → 45
"""

import math
import datetime
import uuid
from typing import Optional
from dataclasses import dataclass
from sqlalchemy.orm import Session
from sqlalchemy import text
from backend.shared.config.logging import logger


# Target chunk memory footprint in bytes (32 MB default)
TARGET_CHUNK_BYTES = 32 * 1024 * 1024

# Minimum and maximum chunk sizes (rows)
MIN_CHUNK_SIZE =     100
MAX_CHUNK_SIZE = 500_000

# Throughput benchmarks (rows/sec) used for ETA estimation only
THROUGHPUT_BENCHMARKS = {
    "mysql_to_mysql":           50_000,
    "mysql_to_postgresql":      30_000,
    "postgresql_to_postgresql": 60_000,
    "default":                  40_000,
}

# If avg row size cannot be determined, assume this default
DEFAULT_AVG_ROW_BYTES = 512


@dataclass
class ChunkPlan:
    table_name:           str
    row_count:            int
    avg_row_size_bytes:   int
    pk_min:               Optional[int]
    pk_max:               Optional[int]
    pk_distribution:      str          # sequential | sparse | uuid | unknown
    computed_chunk_size:  int
    computed_chunk_count: int
    strategy_used:        str          # full_table | size_based | count_based | streaming | uuid_sparse
    estimated_duration_sec: int
    memory_estimate_mb:   int
    notes:                str


class AdaptiveChunkPlanner:

    def compute(
        self,
        table_name:    str,
        source_config: dict,
        db:            Session = None,
        job_id:        str     = None,
        pk_column:     str     = "id",
        source_db_type: str    = "mysql",
        target_db_type: str    = "mysql",
    ) -> ChunkPlan:
        """
        Main entry point. Analyzes the source table and returns
        a ChunkPlan with optimal chunk size and strategy.
        """
        logger.info("Adaptive chunk planning starting", table=table_name)

        # ── Step 1: Get row count ─────────────────────────────────────────────
        row_count = self._get_row_count(source_config, table_name, pk_column)

        # ── Step 2: Get avg row size ──────────────────────────────────────────
        avg_row_bytes = self._get_avg_row_size(source_config, table_name)

        # ── Step 3: Get PK stats ──────────────────────────────────────────────
        pk_min, pk_max, pk_distribution = self._analyze_pk(
            source_config, table_name, pk_column
        )

        # ── Step 4: Select strategy and compute chunk size ────────────────────
        chunk_size, strategy, notes = self._select_strategy(
            row_count=row_count,
            avg_row_bytes=avg_row_bytes,
            pk_distribution=pk_distribution,
        )

        # ── Step 5: Compute derived values ────────────────────────────────────
        chunk_count = math.ceil(row_count / chunk_size) if row_count > 0 else 1

        tput_key    = f"{source_db_type}_to_{target_db_type}"
        throughput  = THROUGHPUT_BENCHMARKS.get(tput_key, THROUGHPUT_BENCHMARKS["default"])
        est_seconds = int((row_count / throughput)) if throughput > 0 else 0
        mem_mb      = int((chunk_size * avg_row_bytes) / (1024 * 1024))

        plan = ChunkPlan(
            table_name=table_name,
            row_count=row_count,
            avg_row_size_bytes=avg_row_bytes,
            pk_min=pk_min,
            pk_max=pk_max,
            pk_distribution=pk_distribution,
            computed_chunk_size=chunk_size,
            computed_chunk_count=chunk_count,
            strategy_used=strategy,
            estimated_duration_sec=est_seconds,
            memory_estimate_mb=mem_mb,
            notes=notes,
        )

        logger.info(
            "Chunk plan computed",
            table=table_name,
            rows=row_count,
            chunk_size=chunk_size,
            chunks=chunk_count,
            strategy=strategy,
            est_minutes=round(est_seconds / 60, 1)
        )

        # ── Step 6: Persist to DB if session provided ─────────────────────────
        if db and job_id:
            self._save_plan(db, job_id, plan)

        return plan

    def compute_all_tables(
        self,
        table_names:   list,
        source_config: dict,
        db:            Session,
        job_id:        str,
        pk_columns:    dict = None,    # {table_name: pk_column}
        source_db_type: str = "mysql",
        target_db_type: str = "mysql",
    ) -> dict:
        """
        Compute chunk plans for all tables in a job.
        Returns {table_name: ChunkPlan}.
        Updates migration_tables rows with computed_chunk_size.
        """
        pk_columns = pk_columns or {}
        plans = {}

        for table_name in table_names:
            pk_col = pk_columns.get(table_name, "id")
            try:
                plan = self.compute(
                    table_name=table_name,
                    source_config=source_config,
                    db=db,
                    job_id=job_id,
                    pk_column=pk_col,
                    source_db_type=source_db_type,
                    target_db_type=target_db_type,
                )
                plans[table_name] = plan

                # Update migration_tables with computed chunk size
                if db:
                    db.execute(
                        text("""
                            UPDATE migration_tables
                            SET computed_chunk_size = :cs,
                                avg_row_size_bytes  = :rs,
                                total_rows          = :rc
                            WHERE job_id = :jid AND table_name = :tname
                        """),
                        {
                            "cs":    plan.computed_chunk_size,
                            "rs":    plan.avg_row_size_bytes,
                            "rc":    plan.row_count,
                            "jid":   job_id,
                            "tname": table_name,
                        }
                    )
            except Exception as e:
                logger.error(
                    "Chunk planning failed for table, using default",
                    table=table_name, error=str(e)
                )
                plans[table_name] = ChunkPlan(
                    table_name=table_name,
                    row_count=0,
                    avg_row_size_bytes=DEFAULT_AVG_ROW_BYTES,
                    pk_min=None, pk_max=None,
                    pk_distribution="unknown",
                    computed_chunk_size=100_000,
                    computed_chunk_count=1,
                    strategy_used="default_fallback",
                    estimated_duration_sec=0,
                    memory_estimate_mb=50,
                    notes="Planning failed — using default 100k chunk size",
                )

        if db:
            try:
                db.commit()
            except Exception:
                db.rollback()

        return plans

    # ── Strategy selector ─────────────────────────────────────────────────────

    def _select_strategy(
        self,
        row_count:       int,
        avg_row_bytes:   int,
        pk_distribution: str,
    ):
        """
        Select the best chunking strategy based on table characteristics.
        Returns (chunk_size, strategy_name, notes).
        """
        # FULL TABLE: tiny tables - no point chunking
        if row_count <= 1_000:
            return (
                max(row_count, 1),
                "full_table",
                f"Small table ({row_count:,} rows) — migrated as single chunk"
            )

        # UUID / SPARSE PKs: can't do pk_range efficiently, use offset-based
        if pk_distribution in ("uuid", "sparse"):
            chunk_size = min(10_000, row_count)
            return (
                chunk_size,
                "uuid_sparse",
                f"UUID/sparse PK detected — using {chunk_size:,} row offset-based chunks"
            )

        # STREAMING: massive tables
        if row_count > 500_000_000:
            return (
                10_000,
                "streaming",
                f"Massive table ({row_count:,} rows) — streaming mode with small chunks for safety"
            )

        # SIZE_BASED: target 32MB per chunk
        if avg_row_bytes > 0:
            size_based = int(TARGET_CHUNK_BYTES / avg_row_bytes)
            size_based = max(MIN_CHUNK_SIZE, min(MAX_CHUNK_SIZE, size_based))

            # If rows are very wide (>4KB avg), use count-based cap
            if avg_row_bytes > 4096:
                count_based = 5_000
                chunk_size  = min(size_based, count_based)
                return (
                    chunk_size,
                    "count_based",
                    f"Wide rows ({avg_row_bytes:,} bytes avg) — capped at {chunk_size:,} rows/chunk"
                )

            return (
                size_based,
                "size_based",
                f"Size-based: {avg_row_bytes} bytes/row → {size_based:,} rows = ~32MB/chunk"
            )

        # Fallback: default 100k
        return (
            100_000,
            "default",
            "Could not determine row size — using default 100k chunk size"
        )

    # ── DB analysis helpers ───────────────────────────────────────────────────

    def _get_row_count(self, config: dict, table_name: str, pk_column: str) -> int:
        conn   = self._connect(config)
        cursor = conn.cursor()
        try:
            engine = config.get("engine", "mysql").lower()
            if engine == "mysql":
                # Use statistics first (fast, approximate)
                cursor.execute(
                    "SELECT TABLE_ROWS FROM information_schema.TABLES "
                    "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s",
                    (config.get("database"), table_name)
                )
                row = cursor.fetchone()
                if row and row[0] and row[0] > 1000:
                    return int(row[0])
                # Exact count for small tables
                cursor.execute(f"SELECT COUNT(*) FROM `{table_name}`")
            else:
                # PostgreSQL: use pg_class estimate first
                cursor.execute(
                    "SELECT reltuples::BIGINT FROM pg_class WHERE relname = %s",
                    (table_name,)
                )
                row = cursor.fetchone()
                if row and row[0] > 1000:
                    return int(row[0])
                cursor.execute(f'SELECT COUNT(*) FROM "{table_name}"')

            result = cursor.fetchone()
            return int(result[0]) if result else 0
        except Exception as e:
            logger.warning("Row count failed", table=table_name, error=str(e))
            return 0
        finally:
            cursor.close()
            conn.close()

    def _get_avg_row_size(self, config: dict, table_name: str) -> int:
        conn   = self._connect(config)
        cursor = conn.cursor()
        engine = config.get("engine", "mysql").lower()
        try:
            if engine == "mysql":
                cursor.execute(
                    "SELECT AVG_ROW_LENGTH FROM information_schema.TABLES "
                    "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s",
                    (config.get("database"), table_name)
                )
                row = cursor.fetchone()
                if row and row[0] and int(row[0]) > 0:
                    return int(row[0])
                # Sample 1000 rows and measure
                cursor.execute(
                    f"SELECT LENGTH(CONCAT_WS(',', *)) FROM `{table_name}` LIMIT 1000"
                )
                rows = cursor.fetchall()
                if rows:
                    total = sum(r[0] for r in rows if r[0])
                    return int(total / len(rows)) if rows else DEFAULT_AVG_ROW_BYTES
            else:
                # PostgreSQL: use pg_relation_size / row count
                cursor.execute(
                    "SELECT pg_relation_size(%s)::BIGINT, reltuples::BIGINT "
                    "FROM pg_class WHERE relname = %s",
                    (table_name, table_name)
                )
                row = cursor.fetchone()
                if row and row[0] and row[1] and int(row[1]) > 0:
                    return int(row[0] / row[1])

            return DEFAULT_AVG_ROW_BYTES
        except Exception as e:
            logger.warning("Avg row size failed", table=table_name, error=str(e))
            return DEFAULT_AVG_ROW_BYTES
        finally:
            cursor.close()
            conn.close()

    def _analyze_pk(self, config: dict, table_name: str, pk_column: str):
        """Returns (pk_min, pk_max, distribution_type)."""
        conn   = self._connect(config)
        cursor = conn.cursor()
        engine = config.get("engine", "mysql").lower()
        try:
            if engine == "mysql":
                cursor.execute(
                    f"SELECT MIN(`{pk_column}`), MAX(`{pk_column}`) FROM `{table_name}`"
                )
            else:
                cursor.execute(
                    f'SELECT MIN("{pk_column}"), MAX("{pk_column}") FROM "{table_name}"'
                )
            row = cursor.fetchone()
            if not row or row[0] is None:
                return None, None, "unknown"

            pk_min_val = row[0]
            pk_max_val = row[1]

            # Detect UUID PKs
            if isinstance(pk_min_val, str) and len(pk_min_val) == 36 and pk_min_val.count("-") == 4:
                return None, None, "uuid"

            try:
                pk_min = int(pk_min_val)
                pk_max = int(pk_max_val)
            except (ValueError, TypeError):
                return None, None, "uuid"

            pk_range = pk_max - pk_min + 1
            row_count = self._get_row_count(config, table_name, pk_column)

            if row_count > 0:
                fill_ratio = row_count / pk_range if pk_range > 0 else 1.0
                if fill_ratio < 0.3:
                    return pk_min, pk_max, "sparse"

            return pk_min, pk_max, "sequential"

        except Exception as e:
            logger.warning("PK analysis failed", table=table_name, error=str(e))
            return None, None, "unknown"
        finally:
            cursor.close()
            conn.close()

    def _save_plan(self, db: Session, job_id: str, plan: ChunkPlan):
        try:
            db.execute(
                text("""
                    INSERT INTO adaptive_chunk_configs
                        (id, job_id, table_name, row_count, avg_row_size_bytes,
                         pk_min, pk_max, pk_distribution,
                         computed_chunk_size, computed_chunk_count,
                         strategy_used, estimated_duration_sec,
                         memory_estimate_mb, created_at)
                    VALUES
                        (:id, :jid, :tname, :rc, :rs,
                         :pmin, :pmax, :pdist,
                         :cs, :cc,
                         :strat, :est_sec,
                         :mem, :now)
                    ON CONFLICT DO NOTHING
                """),
                {
                    "id":      str(uuid.uuid4()),
                    "jid":     job_id,
                    "tname":   plan.table_name,
                    "rc":      plan.row_count,
                    "rs":      plan.avg_row_size_bytes,
                    "pmin":    plan.pk_min,
                    "pmax":    plan.pk_max,
                    "pdist":   plan.pk_distribution,
                    "cs":      plan.computed_chunk_size,
                    "cc":      plan.computed_chunk_count,
                    "strat":   plan.strategy_used,
                    "est_sec": plan.estimated_duration_sec,
                    "mem":     plan.memory_estimate_mb,
                    "now":     datetime.datetime.utcnow(),
                }
            )
            db.commit()
        except Exception as e:
            db.rollback()
            logger.warning("Failed to save chunk plan", error=str(e))

    def _connect(self, config: dict):
        engine = config.get("engine", "mysql").lower()
        if engine == "mysql":
            import mysql.connector
            return mysql.connector.connect(
                host=config.get("host", "localhost"),
                port=int(config.get("port", 3306)),
                database=config.get("database"),
                user=config.get("user"),
                password=config.get("password"),
                connection_timeout=30,
            )
        else:
            import psycopg2
            return psycopg2.connect(
                host=config.get("host", "localhost"),
                port=int(config.get("port", 5432)),
                dbname=config.get("database"),
                user=config.get("user"),
                password=config.get("password"),
                connect_timeout=30,
            )
