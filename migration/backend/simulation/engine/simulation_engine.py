"""
Simulation Engine
File: migration/backend/simulation/engine/simulation_engine.py

What-if calculator. Given hypothetical migration parameters, projects:
  - Estimated duration
  - Rows/sec and MB/sec throughput
  - CPU usage on source and target
  - Network bandwidth consumption
  - Target storage required
  - Failure probability
  - Bottleneck identification
  - Per-table timing breakdown

All without touching any production database.
Uses real data from Metadata Catalog (Part 3) when available,
falls back to formula-based estimates when not.

Design principle: NO new connections, NO queries to source/target.
Pure math on catalog data.

Example scenarios:
  "What if I use 4 workers vs 16?"
  "What if I use size_based chunks instead of count_based?"
  "What if my network bandwidth is limited to 100 Mbps?"
  "What happens if I migrate during business hours vs off-peak?"

Usage:
    engine = SimulationEngine()
    result = engine.simulate(
        db=db,
        connection_id="abc-123",
        worker_count=8,
        chunk_size_strategy="size_based",
        source_engine="mysql",
        target_engine="postgresql",
    )
"""

import math
import json
import datetime
import uuid
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field
from sqlalchemy.orm import Session
from sqlalchemy import text

from backend.shared.config.logging import logger


# ── Throughput model (rows/sec per worker, empirical benchmarks) ───────────────
# These are conservative estimates. Real throughput depends on row size,
# index complexity, network latency, and DB server load.

THROUGHPUT_MODEL = {
    # (source, target): rows/sec/worker at baseline (32MB chunk, no LOB, sequential PK)
    ("mysql",      "mysql"):           12_500,
    ("mysql",      "postgresql"):       7_500,
    ("postgresql", "postgresql"):      15_000,
    ("postgresql", "mysql"):            8_000,
    ("mysql",      "mariadb"):         13_000,
    ("sqlite",     "mysql"):           20_000,
    ("sqlite",     "postgresql"):      18_000,
}

# Adjustment factors (multiplied onto base throughput)
CHUNK_STRATEGY_FACTOR = {
    "full_table":   1.2,    # fastest — single read, no range queries
    "size_based":   1.0,    # baseline
    "count_based":  0.85,   # slightly slower — wide rows need more I/O
    "streaming":    0.70,   # smallest chunks, highest overhead
    "uuid_sparse":  0.75,   # offset-based chunking has overhead
}

# CPU and network usage estimates
SOURCE_CPU_PER_WORKER    = 8.0    # % CPU on source DB per worker
TARGET_CPU_PER_WORKER    = 12.0   # % CPU on target DB per worker (writes cost more)
NETWORK_OVERHEAD_FACTOR  = 1.15   # 15% protocol overhead on top of raw data size
TARGET_STORAGE_FACTOR    = 1.25   # target needs 25% more space (indexes, WAL, temp)


@dataclass
class TableSimResult:
    table_name:      str
    row_count:       int
    size_gb:         float
    chunk_count:     int
    est_seconds:     int
    est_duration:    str
    has_lob:         bool
    bottleneck:      str


@dataclass
class SimulationResult:
    worker_count:               int
    chunk_size_strategy:        str
    estimated_duration_sec:     int
    estimated_duration_str:     str
    estimated_rows_per_sec:     int
    estimated_mb_per_sec:       float
    estimated_cpu_source_pct:   float
    estimated_cpu_target_pct:   float
    estimated_network_gb:       float
    estimated_network_mbps:     float
    estimated_target_storage_gb: float
    failure_probability_pct:    float
    bottleneck:                 str
    total_rows:                 int
    total_size_gb:              float
    table_breakdown:            List[TableSimResult] = field(default_factory=list)
    recommendations:            List[str] = field(default_factory=list)
    data_source:                str = "metadata_catalog"
    warnings:                   List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "worker_count":               self.worker_count,
            "chunk_size_strategy":        self.chunk_size_strategy,
            "estimated_duration_sec":     self.estimated_duration_sec,
            "estimated_duration_str":     self.estimated_duration_str,
            "estimated_rows_per_sec":     self.estimated_rows_per_sec,
            "estimated_mb_per_sec":       round(self.estimated_mb_per_sec, 2),
            "estimated_cpu_source_pct":   round(self.estimated_cpu_source_pct, 1),
            "estimated_cpu_target_pct":   round(self.estimated_cpu_target_pct, 1),
            "estimated_network_gb":       round(self.estimated_network_gb, 3),
            "estimated_network_mbps":     round(self.estimated_network_mbps, 2),
            "estimated_target_storage_gb": round(self.estimated_target_storage_gb, 2),
            "failure_probability_pct":    round(self.failure_probability_pct, 1),
            "bottleneck":                 self.bottleneck,
            "total_rows":                 self.total_rows,
            "total_size_gb":              round(self.total_size_gb, 2),
            "table_breakdown":            [
                {
                    "table_name":   t.table_name,
                    "row_count":    t.row_count,
                    "size_gb":      round(t.size_gb, 3),
                    "chunk_count":  t.chunk_count,
                    "est_seconds":  t.est_seconds,
                    "est_duration": t.est_duration,
                    "has_lob":      t.has_lob,
                    "bottleneck":   t.bottleneck,
                }
                for t in sorted(self.table_breakdown, key=lambda x: x.est_seconds, reverse=True)[:20]
            ],
            "recommendations": self.recommendations,
            "warnings":        self.warnings,
            "data_source":     self.data_source,
        }


class SimulationEngine:

    def simulate(
        self,
        db:                  Session,
        worker_count:        int,
        connection_id:       Optional[str] = None,
        chunk_size_strategy: str = "size_based",
        chunk_size_override: Optional[int] = None,
        source_engine:       str = "mysql",
        target_engine:       str = "mysql",
        network_bandwidth_mbps: Optional[float] = None,  # None = unlimited
        table_names:         Optional[List[str]] = None,
        manual_tables:       Optional[List[Dict]] = None,
        # [{"table_name": "orders", "row_count": 5000000, "size_gb": 50.0}]
        tenant_id:           str = "local",
        name:                Optional[str] = None,
    ) -> SimulationResult:
        """
        Run a simulation. Returns projected metrics without touching any database.

        Data sources (in priority order):
          1. manual_tables   — caller supplies explicit row counts / sizes
          2. connection_id   — loads from Metadata Catalog (Part 3 must have run)
          3. defaults        — formula-based fallback
        """
        logger.info("Simulation starting",
                    workers=worker_count, strategy=chunk_size_strategy)

        # ── Load table data ───────────────────────────────────────────────
        tables_data, data_source = self._load_table_data(
            db, connection_id, table_names, manual_tables
        )

        if not tables_data:
            # Nothing to simulate — return empty result
            return SimulationResult(
                worker_count=worker_count,
                chunk_size_strategy=chunk_size_strategy,
                estimated_duration_sec=0,
                estimated_duration_str="No data",
                estimated_rows_per_sec=0,
                estimated_mb_per_sec=0,
                estimated_cpu_source_pct=0,
                estimated_cpu_target_pct=0,
                estimated_network_gb=0,
                estimated_network_mbps=0,
                estimated_target_storage_gb=0,
                failure_probability_pct=0,
                bottleneck="none",
                total_rows=0,
                total_size_gb=0,
                recommendations=["No table data found. Run POST /intelligence/scans first "
                                "or provide manual_tables in the request."],
                data_source=data_source,
            )

        # ── Base throughput ───────────────────────────────────────────────
        tput_key  = (source_engine.lower(), target_engine.lower())
        base_tput = THROUGHPUT_MODEL.get(tput_key,
                    THROUGHPUT_MODEL.get(("mysql", "mysql"), 10_000))
        strat_factor = CHUNK_STRATEGY_FACTOR.get(chunk_size_strategy, 1.0)
        tput_per_worker = base_tput * strat_factor
        total_tput      = tput_per_worker * worker_count

        # ── Per-table simulation ──────────────────────────────────────────
        table_results: List[TableSimResult] = []
        total_rows    = 0
        total_size_gb = 0.0

        for td in tables_data:
            tname    = td["table_name"]
            rc       = td.get("row_count", 0) or 0
            sg       = td.get("size_gb", 0) or 0
            has_lob  = td.get("has_lob", False)
            avg_row  = td.get("avg_row_bytes", 512) or 512

            total_rows    += rc
            total_size_gb += sg

            # Adjust throughput for LOB tables
            effective_tput = total_tput
            if has_lob:
                effective_tput = total_tput * 0.3  # LOB tables are ~3x slower
                bottleneck = "source_io_lob"
            else:
                bottleneck = self._identify_bottleneck(
                    worker_count, sg, network_bandwidth_mbps
                )

            t_seconds = int(rc / effective_tput) if effective_tput > 0 and rc > 0 else 0

            # Chunk count estimate
            if chunk_size_override:
                chunk_size = chunk_size_override
            else:
                chunk_size = self._compute_chunk_size(avg_row, chunk_size_strategy, rc)
            chunk_count = math.ceil(rc / chunk_size) if chunk_size > 0 and rc > 0 else 1

            table_results.append(TableSimResult(
                table_name=tname,
                row_count=rc,
                size_gb=sg,
                chunk_count=chunk_count,
                est_seconds=t_seconds,
                est_duration=self._fmt(t_seconds),
                has_lob=has_lob,
                bottleneck=bottleneck,
            ))

        # ── Total duration ────────────────────────────────────────────────
        # Tables run in parallel (up to worker_count), serialized by FK depth.
        # Simplification: assume tables share workers evenly.
        # FK-aware serialization adds ~20% overhead on average.
        raw_seconds  = int(total_rows / total_tput) if total_tput > 0 else 0
        fk_overhead  = 1.2 if len(tables_data) > 5 else 1.0
        est_seconds  = int(raw_seconds * fk_overhead)

        # Network bandwidth constraint
        network_gb   = total_size_gb * NETWORK_OVERHEAD_FACTOR
        if network_bandwidth_mbps:
            network_seconds = int((network_gb * 1024) / (network_bandwidth_mbps / 8))
            if network_seconds > est_seconds:
                est_seconds = network_seconds

        # ── Resource estimates ────────────────────────────────────────────
        rows_per_sec = int(total_rows / est_seconds) if est_seconds > 0 else 0
        mb_per_sec   = (total_size_gb * 1024) / est_seconds if est_seconds > 0 else 0
        net_mbps     = (network_gb * 1024 * 8) / est_seconds if est_seconds > 0 else 0

        cpu_source   = min(SOURCE_CPU_PER_WORKER * worker_count, 95.0)
        cpu_target   = min(TARGET_CPU_PER_WORKER * worker_count, 95.0)
        storage_gb   = total_size_gb * TARGET_STORAGE_FACTOR

        # ── Failure probability ───────────────────────────────────────────
        failure_pct  = self._estimate_failure_probability(
            est_seconds, total_size_gb, worker_count,
            cpu_source, cpu_target, len(table_results)
        )

        # ── Bottleneck ────────────────────────────────────────────────────
        bottleneck   = self._identify_bottleneck(
            worker_count, total_size_gb, network_bandwidth_mbps
        )
        if cpu_source >= 85:
            bottleneck = "source_cpu"
        elif cpu_target >= 85:
            bottleneck = "target_write_cpu"
        elif network_bandwidth_mbps and net_mbps >= network_bandwidth_mbps * 0.9:
            bottleneck = "network_bandwidth"

        # ── Recommendations ───────────────────────────────────────────────
        recommendations = self._build_recommendations(
            worker_count, est_seconds, cpu_source, cpu_target,
            failure_pct, bottleneck, table_results, network_bandwidth_mbps
        )

        result = SimulationResult(
            worker_count=worker_count,
            chunk_size_strategy=chunk_size_strategy,
            estimated_duration_sec=est_seconds,
            estimated_duration_str=self._fmt(est_seconds),
            estimated_rows_per_sec=rows_per_sec,
            estimated_mb_per_sec=mb_per_sec,
            estimated_cpu_source_pct=cpu_source,
            estimated_cpu_target_pct=cpu_target,
            estimated_network_gb=round(network_gb, 3),
            estimated_network_mbps=round(net_mbps, 2),
            estimated_target_storage_gb=round(storage_gb, 2),
            failure_probability_pct=failure_pct,
            bottleneck=bottleneck,
            total_rows=total_rows,
            total_size_gb=round(total_size_gb, 2),
            table_breakdown=table_results,
            recommendations=recommendations,
            data_source=data_source,
        )

        # ── Persist ───────────────────────────────────────────────────────
        self._save(db, result, connection_id, tenant_id, name)

        logger.info("Simulation complete",
                    workers=worker_count,
                    duration=result.estimated_duration_str,
                    failure_pct=failure_pct,
                    bottleneck=bottleneck)

        return result

    def compare(
        self,
        db:              Session,
        connection_id:   str,
        scenarios:       List[Dict[str, Any]],
        source_engine:   str = "mysql",
        target_engine:   str = "mysql",
        tenant_id:       str = "local",
    ) -> Dict[str, Any]:
        """
        Run multiple scenarios and compare them side by side.
        scenarios = [
          {"name": "4 workers", "worker_count": 4, "chunk_size_strategy": "size_based"},
          {"name": "8 workers", "worker_count": 8, "chunk_size_strategy": "size_based"},
          {"name": "16 workers streaming", "worker_count": 16, "chunk_size_strategy": "streaming"},
        ]
        Returns results sorted by duration (fastest first).
        """
        results = []
        for s in scenarios:
            r = self.simulate(
                db=db,
                connection_id=connection_id,
                worker_count=s.get("worker_count", 4),
                chunk_size_strategy=s.get("chunk_size_strategy", "size_based"),
                chunk_size_override=s.get("chunk_size_override"),
                source_engine=source_engine,
                target_engine=target_engine,
                network_bandwidth_mbps=s.get("network_bandwidth_mbps"),
                tenant_id=tenant_id,
                name=s.get("name"),
            )
            d = r.to_dict()
            d["scenario_name"] = s.get("name", f"{s.get('worker_count', 4)} workers")
            results.append(d)

        results.sort(key=lambda x: x["estimated_duration_sec"])
        fastest = results[0] if results else None

        return {
            "scenarios":         len(results),
            "fastest_scenario":  fastest["scenario_name"] if fastest else None,
            "fastest_duration":  fastest["estimated_duration_str"] if fastest else None,
            "comparison":        results,
        }

    # ── Private helpers ───────────────────────────────────────────────────────

    def _load_table_data(
        self,
        db:             Session,
        connection_id:  Optional[str],
        table_names:    Optional[List[str]],
        manual_tables:  Optional[List[Dict]],
    ):
        # Manual input takes priority
        if manual_tables:
            return manual_tables, "manual_input"

        # Load from Metadata Catalog
        if connection_id:
            conditions = ["connection_id = :cid", "catalog_type = 'statistics'"]
            params: Dict[str, Any] = {"cid": connection_id}
            if table_names:
                conditions.append("table_name = ANY(:tnames)")
                params["tnames"] = table_names

            where = " AND ".join(conditions)
            rows  = db.execute(
                text(f"""
                    SELECT DISTINCT ON (table_name)
                        table_name, data
                    FROM metadata_catalog
                    WHERE {where}
                    ORDER BY table_name, computed_at DESC
                """),
                params
            ).fetchall()

            if rows:
                result = []
                for row in rows:
                    data = row.data
                    if isinstance(data, str):
                        try:
                            import json as _json
                            data = _json.loads(data)
                        except Exception:
                            data = {}

                    # Also load LOB detection
                    lob_row = db.execute(
                        text("""
                            SELECT data FROM metadata_catalog
                            WHERE connection_id=:cid AND table_name=:tname
                            AND catalog_type='lob_detection'
                            ORDER BY computed_at DESC LIMIT 1
                        """),
                        {"cid": connection_id, "tname": row.table_name}
                    ).fetchone()

                    has_lob = False
                    if lob_row:
                        lob_data = lob_row.data
                        if isinstance(lob_data, str):
                            try:
                                import json as _json
                                lob_data = _json.loads(lob_data)
                            except Exception:
                                lob_data = {}
                        has_lob = lob_data.get("has_lob", False)

                    result.append({
                        "table_name":    row.table_name,
                        "row_count":     data.get("row_count", 0) or 0,
                        "size_gb":       data.get("size_gb", 0) or 0,
                        "avg_row_bytes": data.get("avg_row_bytes", 512) or 512,
                        "has_lob":       has_lob,
                    })
                return result, "metadata_catalog"

        return [], "no_data"

    def _compute_chunk_size(self, avg_row_bytes: int, strategy: str, row_count: int) -> int:
        TARGET_BYTES = 32 * 1024 * 1024   # 32MB
        if strategy == "full_table":
            return max(row_count, 1)
        if strategy in ("streaming", "uuid_sparse"):
            return 10_000
        if strategy == "count_based":
            return 5_000
        # size_based
        if avg_row_bytes > 0:
            size_based = int(TARGET_BYTES / avg_row_bytes)
            return max(100, min(500_000, size_based))
        return 100_000

    def _identify_bottleneck(self, workers, size_gb, net_mbps) -> str:
        if net_mbps and size_gb > 0:
            transfer_time = (size_gb * 1024 * 8) / net_mbps   # seconds
            if transfer_time > size_gb * 10:   # network is clearly the limit
                return "network_bandwidth"

        cpu_source = SOURCE_CPU_PER_WORKER * workers
        cpu_target = TARGET_CPU_PER_WORKER * workers

        if cpu_target >= 80:
            return "target_write_cpu"
        if cpu_source >= 80:
            return "source_cpu"
        if size_gb > 1000:
            return "source_io"
        return "balanced"

    def _estimate_failure_probability(
        self, duration_sec, size_gb, workers, cpu_src, cpu_tgt, table_count
    ) -> float:
        """
        Heuristic failure probability estimate. Higher for:
        - Very long migrations (more time = more chance of transient failure)
        - High CPU usage (increases DB instability risk)
        - Large datasets (more I/O = more chance of timeout/network blip)
        """
        base = 2.0    # baseline 2% for any migration
        if duration_sec > 86400:  base += 5.0    # > 24 hours
        if duration_sec > 43200:  base += 3.0    # > 12 hours
        if cpu_src >= 85:         base += 8.0    # source CPU saturated
        if cpu_tgt >= 85:         base += 8.0    # target CPU saturated
        if size_gb >= 10000:      base += 5.0    # > 10 TB
        if table_count >= 100:    base += 2.0    # many tables
        return min(round(base, 1), 95.0)

    def _build_recommendations(
        self, workers, est_seconds, cpu_src, cpu_tgt,
        failure_pct, bottleneck, table_results, net_mbps
    ) -> List[str]:
        recs = []

        if bottleneck == "source_cpu":
            recs.append(f"Source DB CPU is projected at {cpu_src:.0f}% — reduce to ≤ {workers//2} workers "
                        "or schedule during off-peak hours to avoid impacting production.")
        if bottleneck == "target_write_cpu":
            recs.append(f"Target DB CPU is projected at {cpu_tgt:.0f}% — consider batching inserts "
                        "or increasing target instance size.")
        if bottleneck == "network_bandwidth":
            recs.append("Network bandwidth is the bottleneck. Increase bandwidth or migrate "
                        "during low-traffic hours.")
        if bottleneck == "source_io_lob":
            recs.append("LOB columns are slowing migration significantly. Consider migrating "
                        "LOB tables separately with smaller chunk sizes.")

        if failure_pct > 20:
            recs.append(f"Failure probability is {failure_pct:.0f}%. Enable CDC mode for "
                        "large/long-running migrations to reduce risk of having to restart.")
        if failure_pct > 10:
            recs.append("Generate rollback plan before starting: POST /jobs/{id}/rollback/generate")

        lob_tables = [t.table_name for t in table_results if t.has_lob]
        if lob_tables:
            recs.append(f"LOB tables {lob_tables[:3]} will be significantly slower. "
                        "Consider migrating these last and using streaming chunk strategy.")

        slow_tables = [t for t in table_results if t.est_seconds > 3600]
        if slow_tables:
            recs.append(f"{len(slow_tables)} table(s) will each take >1 hour. "
                        "Monitor these specifically: " +
                        ", ".join(t.table_name for t in slow_tables[:3]))

        if est_seconds > 28800 and workers < 8:
            recs.append(f"Migration estimated at {self._fmt(est_seconds)} with {workers} workers. "
                        "Try simulating with 8+ workers to reduce duration.")

        if not recs:
            recs.append("No major concerns detected. Migration parameters look reasonable.")

        return recs

    def _save(self, db, result: SimulationResult, connection_id, tenant_id, name):
        import json as _json
        try:
            db.execute(
                text("""
                    INSERT INTO simulation_runs
                        (id, tenant_id, connection_id, name,
                         worker_count, chunk_size_strategy,
                         estimated_duration_sec, estimated_duration_str,
                         estimated_rows_per_sec, estimated_mb_per_sec,
                         estimated_cpu_pct, estimated_network_gb,
                         estimated_target_storage_gb, failure_probability_pct,
                         bottleneck, table_breakdown, recommendations,
                         data_source, created_at)
                    VALUES
                        (gen_random_uuid(), :tid, :cid, :name,
                         :workers, :strategy,
                         :dur_sec, :dur_str,
                         :rps, :mbps,
                         :cpu, :net_gb,
                         :storage_gb, :fail_pct,
                         :bottleneck, :breakdown::jsonb, :recs::jsonb,
                         :ds, :now)
                """),
                {
                    "tid":      tenant_id,
                    "cid":      connection_id,
                    "name":     name,
                    "workers":  result.worker_count,
                    "strategy": result.chunk_size_strategy,
                    "dur_sec":  result.estimated_duration_sec,
                    "dur_str":  result.estimated_duration_str,
                    "rps":      result.estimated_rows_per_sec,
                    "mbps":     result.estimated_mb_per_sec,
                    "cpu":      result.estimated_cpu_source_pct,
                    "net_gb":   result.estimated_network_gb,
                    "storage_gb": result.estimated_target_storage_gb,
                    "fail_pct": result.failure_probability_pct,
                    "bottleneck": result.bottleneck,
                    "breakdown": _json.dumps([
                        {"table_name": t.table_name, "est_seconds": t.est_seconds,
                         "size_gb": t.size_gb, "has_lob": t.has_lob}
                        for t in result.table_breakdown[:20]
                    ]),
                    "recs": _json.dumps(result.recommendations),
                    "ds":   result.data_source,
                    "now":  datetime.datetime.utcnow(),
                }
            )
            db.commit()
        except Exception as e:
            logger.warning("Failed to save simulation run", error=str(e))
            db.rollback()

    def _fmt(self, seconds: int) -> str:
        if seconds <= 0:    return "< 1 minute"
        if seconds < 60:    return f"{seconds}s"
        if seconds < 3600:  m, s = divmod(seconds, 60);   return f"{m}m {s}s"
        if seconds < 86400: h, r = divmod(seconds, 3600); return f"{h}h {r//60}m"
        d, r = divmod(seconds, 86400); return f"{d}d {r//3600}h"
