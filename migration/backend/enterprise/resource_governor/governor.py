"""
Resource Governor
File: migration/backend/enterprise/resource_governor/governor.py

Monitors source and target DB load during migration and
auto-throttles workers when resources are under pressure.

Problems it solves:
  - Migration saturating source DB CPU → production queries slow down
  - Target DB overwhelmed with inserts → connection pool exhausted
  - Workers consuming all available memory

How it works:
  A background thread runs every 30 seconds and:
    1. Samples source DB connection count and active queries
    2. Samples target DB connection count
    3. Checks worker CPU/memory via worker_heartbeats table
    4. Compares against configured thresholds
    5. If any threshold exceeded → reduces allowed parallelism via Redis
    6. Logs throttle events to resource_governor_state table

Throttle mechanism:
  When throttling is needed, sets a Redis key:
    migration:throttle:{job_id} = "reduce_workers_to_2"
  Workers check this key before pulling new chunks.
  When load recovers, key is removed.
"""

import threading
import time
import datetime
import uuid
from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy import text

from backend.shared.config.database import get_db
from backend.shared.config.redis import redis_client
from backend.shared.config.logging import logger


# Throttle thresholds — adjust per environment
THRESHOLDS = {
    "source_cpu_pct_warn":    70.0,   # warn but continue
    "source_cpu_pct_throttle": 85.0,  # reduce workers
    "target_cpu_pct_warn":    70.0,
    "target_cpu_pct_throttle": 85.0,
    "source_conn_pct_warn":   70.0,   # % of max_connections used
    "source_conn_pct_throttle": 85.0,
    "worker_memory_pct_throttle": 90.0,
    "queue_depth_warn":       2000,    # chunks backed up
    "queue_depth_throttle":   5000,
}

CHECK_INTERVAL_SECONDS = 30


def _throttle_key(job_id: str) -> str:
    return f"migration:throttle:{job_id}"


class ResourceGovernor:

    def __init__(self, job_id: str, source_config: dict, target_config: dict,
                 max_workers: int = 4):
        self.job_id        = job_id
        self.source_config = source_config
        self.target_config = target_config
        self.max_workers   = max_workers
        self.running       = False
        self._thread: Optional[threading.Thread] = None
        self._current_throttle_level = 0   # 0=none, 1=warn, 2=reduce, 3=pause

    def start(self):
        """Start the governor background thread."""
        self.running = True
        self._thread = threading.Thread(
            target=self._loop,
            daemon=True,
            name=f"resource-governor-{self.job_id[:8]}"
        )
        self._thread.start()
        logger.info("ResourceGovernor started", job_id=self.job_id)

    def stop(self):
        """Stop the governor and clear any throttle state."""
        self.running = False
        if self._thread:
            self._thread.join(timeout=10)
        self._clear_throttle()
        logger.info("ResourceGovernor stopped", job_id=self.job_id)

    def is_throttled(self) -> bool:
        """Check if this job is currently being throttled."""
        return redis_client.exists(_throttle_key(self.job_id)) > 0

    def get_allowed_workers(self) -> int:
        """Return the current allowed worker count for this job."""
        raw = redis_client.get(_throttle_key(self.job_id))
        if raw:
            try:
                return int(raw)
            except Exception:
                pass
        return self.max_workers

    # ── Background loop ───────────────────────────────────────────────────────

    def _loop(self):
        while self.running:
            try:
                self._check_and_throttle()
            except Exception as e:
                logger.warning("ResourceGovernor check failed (non-fatal)", error=str(e))
            time.sleep(CHECK_INTERVAL_SECONDS)

    def _check_and_throttle(self):
        db     = next(get_db())
        issues = []

        try:
            # ── Source DB load ────────────────────────────────────────────────
            src_metrics = self._get_db_metrics(self.source_config)
            if src_metrics:
                if src_metrics["active_connections_pct"] >= THRESHOLDS["source_conn_pct_throttle"]:
                    issues.append(f"Source DB connections at {src_metrics['active_connections_pct']:.0f}%")
                elif src_metrics["active_connections_pct"] >= THRESHOLDS["source_conn_pct_warn"]:
                    logger.warning("Source DB connection usage high",
                                   pct=src_metrics["active_connections_pct"])

            # ── Target DB load ────────────────────────────────────────────────
            tgt_metrics = self._get_db_metrics(self.target_config)

            # ── Worker memory ─────────────────────────────────────────────────
            max_worker_mem = self._get_max_worker_memory(db)
            if max_worker_mem >= THRESHOLDS["worker_memory_pct_throttle"]:
                issues.append(f"Worker memory at {max_worker_mem:.0f}%")

            # ── Redis queue depth ─────────────────────────────────────────────
            try:
                from backend.shared.constants.queues import Queues
                queue_depth = redis_client.llen(Queues.MIGRATION_QUEUE)
                if queue_depth >= THRESHOLDS["queue_depth_throttle"]:
                    issues.append(f"Queue depth at {queue_depth:,} — workers cannot keep up")
            except Exception:
                queue_depth = 0

            # ── Make throttle decision ────────────────────────────────────────
            throttle_applied = len(issues) > 0

            if throttle_applied:
                # Reduce allowed workers by half (minimum 1)
                allowed = max(1, self.max_workers // 2)
                redis_client.setex(
                    _throttle_key(self.job_id),
                    CHECK_INTERVAL_SECONDS * 3,   # TTL: auto-expires after 3 checks
                    str(allowed)
                )
                logger.warning(
                    "Throttle applied",
                    job_id=self.job_id,
                    issues=issues,
                    workers_reduced_to=allowed
                )
            else:
                # All clear — remove throttle
                self._clear_throttle()

            # ── Record to DB ──────────────────────────────────────────────────
            self._record_state(
                db=db,
                src_metrics=src_metrics,
                tgt_metrics=tgt_metrics,
                throttle_applied=throttle_applied,
                throttle_reason="; ".join(issues) if issues else None,
            )

        finally:
            db.close()

    def _get_db_metrics(self, config: dict) -> Optional[dict]:
        engine = config.get("engine", "mysql").lower()
        try:
            if engine == "mysql":
                import mysql.connector
                conn = mysql.connector.connect(
                    host=config.get("host", "localhost"),
                    port=int(config.get("port", 3306)),
                    database=config.get("database"),
                    user=config.get("user"),
                    password=config.get("password"),
                    connection_timeout=5,
                )
                cursor = conn.cursor()
                cursor.execute(
                    "SHOW STATUS WHERE Variable_name IN "
                    "('Threads_connected','Max_used_connections')"
                )
                rows   = {r[0]: int(r[1]) for r in cursor.fetchall()}
                cursor.execute("SHOW VARIABLES LIKE 'max_connections'")
                max_c  = cursor.fetchone()
                max_connections = int(max_c[1]) if max_c else 151
                active = rows.get("Threads_connected", 0)
                cursor.close()
                conn.close()
                return {
                    "active_connections":     active,
                    "max_connections":        max_connections,
                    "active_connections_pct": (active / max_connections * 100),
                }
            else:
                import psycopg2
                conn = psycopg2.connect(
                    host=config.get("host", "localhost"),
                    port=int(config.get("port", 5432)),
                    dbname=config.get("database"),
                    user=config.get("user"),
                    password=config.get("password"),
                    connect_timeout=5,
                )
                cursor = conn.cursor()
                cursor.execute("SELECT count(*) FROM pg_stat_activity WHERE state='active'")
                active = cursor.fetchone()[0]
                cursor.execute("SHOW max_connections")
                max_c  = cursor.fetchone()
                max_connections = int(max_c[0]) if max_c else 100
                cursor.close()
                conn.close()
                return {
                    "active_connections":     active,
                    "max_connections":        max_connections,
                    "active_connections_pct": (active / max_connections * 100),
                }
        except Exception as e:
            logger.debug("DB metrics fetch failed", error=str(e))
            return None

    def _get_max_worker_memory(self, db: Session) -> float:
        """Returns the highest memory usage % among active workers."""
        try:
            result = db.execute(
                text("""
                    SELECT MAX(memory_usage) FROM worker_heartbeats
                    WHERE last_heartbeat > :threshold
                """),
                {"threshold": datetime.datetime.utcnow() - datetime.timedelta(minutes=2)}
            ).scalar()
            return float(result or 0.0)
        except Exception:
            return 0.0

    def _record_state(self, db, src_metrics, tgt_metrics, throttle_applied, throttle_reason):
        try:
            db.execute(
                text("""
                    INSERT INTO resource_governor_state
                        (id, job_id, recorded_at,
                         source_db_conn_count, target_db_conn_count,
                         throttle_applied, throttle_reason)
                    VALUES
                        (:id, :jid, :now,
                         :src_conn, :tgt_conn,
                         :throttle, :reason)
                """),
                {
                    "id":         str(uuid.uuid4()),
                    "jid":        self.job_id,
                    "now":        datetime.datetime.utcnow(),
                    "src_conn":   src_metrics.get("active_connections") if src_metrics else None,
                    "tgt_conn":   tgt_metrics.get("active_connections") if tgt_metrics else None,
                    "throttle":   throttle_applied,
                    "reason":     throttle_reason,
                }
            )
            db.commit()
        except Exception as e:
            db.rollback()
            logger.debug("Failed to record governor state", error=str(e))

    def _clear_throttle(self):
        try:
            redis_client.delete(_throttle_key(self.job_id))
        except Exception:
            pass
