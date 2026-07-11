"""
Dependency Graph Executor
File: migration/backend/enterprise/dependency_graph/graph_executor.py

Builds and executes a FK-aware dependency graph for table migration order.

The old plan_generator did topological sort for PLANNING only.
This module drives actual EXECUTION — it publishes chunks to Redis
in the correct order, waiting for each level to complete before
starting the next.

Example DB schema:
    country → state → city → customer → orders → order_items

Without dependency graph:
    All tables queued simultaneously → FK violations → failure

With dependency graph:
    Level 0: country              (no deps)         → migrate first
    Level 1: state                (depends on country) → wait for level 0
    Level 2: city                 (depends on state)   → wait for level 1
    Level 3: customer             (depends on city)    → wait for level 2
    Level 4: orders               (depends on customer)→ wait for level 3
    Level 5: order_items          (depends on orders)  → wait for level 4

Tables at the same level can migrate in PARALLEL.
"""

import time
import datetime
import json
import uuid
from typing import Dict, List, Optional, Set
from dataclasses import dataclass, field
from sqlalchemy.orm import Session
from sqlalchemy import text, func

from backend.shared.config.logging import logger
from backend.shared.config.redis import redis_client
from backend.shared.constants.queues import Queues
from backend.control_plane.app.models.migration import MigrationTable, MigrationChunk


@dataclass
class TableNode:
    table_name:    str
    depends_on:    List[str] = field(default_factory=list)
    depth_level:   int = 0
    can_parallel:  bool = True
    table_id:      Optional[str] = None


@dataclass
class DependencyGraph:
    job_id:         str
    nodes:          Dict[str, TableNode]       # table_name → TableNode
    levels:         Dict[int, List[str]]       # depth_level → [table_names]
    max_depth:      int = 0
    has_cycles:     bool = False
    cycle_info:     List[str] = field(default_factory=list)


class DependencyGraphExecutor:

    def build_graph(
        self,
        db:            Session,
        job_id:        str,
        source_schema: Dict,
        table_names:   List[str] = None,
    ) -> DependencyGraph:
        """
        Build the FK dependency graph from the source schema.
        Persist it to table_dependency_graph.
        """
        src_tables = source_schema.get("tables", {})
        if table_names:
            src_tables = {k: v for k, v in src_tables.items() if k in table_names}

        table_set = set(src_tables.keys())
        nodes     = {}

        # Build adjacency: table → set of tables it depends on
        for tname, tdata in src_tables.items():
            deps = []
            for fk in tdata.get("foreign_keys", []):
                ref = fk.get("ref_table")
                if ref and ref in table_set and ref != tname:
                    deps.append(ref)
            nodes[tname] = TableNode(table_name=tname, depends_on=list(set(deps)))

        # Topological sort with cycle detection (Kahn's algorithm)
        levels, has_cycles, cycle_info = self._kahn_sort(nodes, table_set)

        graph = DependencyGraph(
            job_id=job_id,
            nodes=nodes,
            levels=levels,
            max_depth=max(levels.keys()) if levels else 0,
            has_cycles=has_cycles,
            cycle_info=cycle_info,
        )

        # Assign depth levels to nodes
        for depth, tables in levels.items():
            for tname in tables:
                if tname in nodes:
                    nodes[tname].depth_level = depth

        # Persist to DB
        self._save_graph(db, job_id, nodes, levels)

        logger.info(
            "Dependency graph built",
            job_id=job_id,
            tables=len(nodes),
            levels=graph.max_depth + 1,
            has_cycles=has_cycles,
        )

        return graph

    def execute_in_order(
        self,
        db:              Session,
        job_id:          str,
        graph:           DependencyGraph,
        chunk_plans:     Dict,         # {table_name: ChunkPlan}
        poll_interval:   int = 10,     # seconds between completion checks
        level_timeout:   int = 86400,  # max seconds to wait per level (24h)
    ):
        """
        Execute migration level by level.
        Within each level, all tables run in parallel.
        Waits for all tables in a level to complete before starting next.
        """
        logger.info(
            "Starting dependency-ordered execution",
            job_id=job_id,
            total_levels=graph.max_depth + 1
        )

        for depth in range(graph.max_depth + 1):
            tables_at_level = graph.levels.get(depth, [])
            if not tables_at_level:
                continue

            logger.info(
                f"Starting level {depth}",
                job_id=job_id,
                tables=tables_at_level
            )

            # Queue all chunks for tables at this level
            for table_name in tables_at_level:
                self._queue_table_chunks(db, job_id, table_name, chunk_plans)

            # Wait for all tables at this level to complete
            self._wait_for_level_completion(
                db=db,
                job_id=job_id,
                tables=tables_at_level,
                poll_interval=poll_interval,
                timeout=level_timeout,
            )

            logger.info(f"Level {depth} completed", job_id=job_id, tables=tables_at_level)

        logger.info("All levels completed", job_id=job_id)

    def get_execution_order(self, graph: DependencyGraph) -> List[Dict]:
        """
        Returns the execution order as a human-readable list.
        Useful for dry-run display and plan generation.
        """
        order = []
        for depth in sorted(graph.levels.keys()):
            tables = graph.levels[depth]
            order.append({
                "level":       depth,
                "tables":      tables,
                "parallel":    len(tables) > 1,
                "description": f"Level {depth}: migrate {', '.join(tables)}" +
                               (" (in parallel)" if len(tables) > 1 else ""),
            })
        return order

    # ── Private helpers ───────────────────────────────────────────────────────

    def _kahn_sort(
        self,
        nodes:    Dict[str, TableNode],
        all_tables: Set[str],
    ):
        """
        Kahn's algorithm for topological sort with level tracking.
        Returns (levels_dict, has_cycles, cycle_tables).
        levels_dict = {0: [independent_tables], 1: [tables_dep_on_level_0], ...}
        """
        in_degree = {t: 0 for t in all_tables}
        adj       = {t: [] for t in all_tables}  # t → tables that depend on t

        for tname, node in nodes.items():
            for dep in node.depends_on:
                if dep in all_tables:
                    in_degree[tname] += 1
                    adj[dep].append(tname)

        # Start with tables that have no dependencies
        queue  = [t for t in all_tables if in_degree[t] == 0]
        levels: Dict[int, List[str]] = {}
        visited = set()
        depth   = 0

        while queue:
            levels[depth] = sorted(queue)  # sort for determinism
            visited.update(queue)
            next_queue = []
            for node_name in queue:
                for dependent in adj[node_name]:
                    in_degree[dependent] -= 1
                    if in_degree[dependent] == 0:
                        next_queue.append(dependent)
            queue = list(set(next_queue))  # deduplicate
            depth += 1

        # Cycle detection: any table not visited has a cycle
        unvisited   = all_tables - visited
        has_cycles  = len(unvisited) > 0
        cycle_info  = list(unvisited)

        # Add cyclic tables as a final level (they'll be migrated last with warnings)
        if unvisited:
            levels[depth] = sorted(unvisited)
            logger.warning(
                "Circular FK dependencies detected — tables will migrate last",
                tables=list(unvisited)
            )

        return levels, has_cycles, cycle_info

    def _queue_table_chunks(
        self,
        db:          Session,
        job_id:      str,
        table_name:  str,
        chunk_plans: Dict,
    ):
        """Push all PENDING chunks for a table onto the Redis queue."""
        chunks = db.query(MigrationChunk).filter(
            MigrationChunk.job_id == job_id,
            MigrationChunk.table_name == table_name,
            MigrationChunk.status == "pending",
        ).all()

        queued = 0
        for chunk in chunks:
            message = {
                "job_id":   str(job_id),
                "table_id": str(chunk.table_id),
                "chunk_id": str(chunk.id),
                "priority": 1,
            }
            redis_client.lpush(Queues.MIGRATION_QUEUE, json.dumps(message))
            queued += 1

        logger.info(
            "Chunks queued for table",
            table=table_name,
            job_id=job_id,
            queued=queued
        )

    def _wait_for_level_completion(
        self,
        db:             Session,
        job_id:         str,
        tables:         List[str],
        poll_interval:  int,
        timeout:        int,
    ):
        """
        Block until all chunks for the given tables are completed or failed.
        Polls every poll_interval seconds.
        """
        start  = time.time()
        while True:
            elapsed = time.time() - start
            if elapsed > timeout:
                logger.error(
                    "Level timed out waiting for completion",
                    job_id=job_id,
                    tables=tables,
                    elapsed_hours=round(elapsed / 3600, 1)
                )
                break

            # Check completion status
            all_done  = True
            for tname in tables:
                pending = db.query(func.count(MigrationChunk.id)).filter(
                    MigrationChunk.job_id == job_id,
                    MigrationChunk.table_name == tname,
                    MigrationChunk.status.in_(["pending", "running", "retrying"]),
                ).scalar() or 0

                if pending > 0:
                    all_done = False
                    break

            if all_done:
                return

            logger.debug(
                "Waiting for level completion",
                job_id=job_id,
                tables=tables,
                elapsed_sec=int(elapsed)
            )
            db.expire_all()  # Refresh SQLAlchemy cache
            time.sleep(poll_interval)

    def _save_graph(
        self,
        db:     Session,
        job_id: str,
        nodes:  Dict[str, TableNode],
        levels: Dict[int, List[str]],
    ):
        # Clear existing graph for this job
        db.execute(
            text("DELETE FROM table_dependency_graph WHERE job_id = :jid"),
            {"jid": job_id}
        )

        exec_order = 0
        for depth in sorted(levels.keys()):
            for tname in levels[depth]:
                node = nodes.get(tname, TableNode(table_name=tname))
                db.execute(
                    text("""
                        INSERT INTO table_dependency_graph
                            (id, job_id, table_name, depends_on, depth_level,
                             execution_order, can_parallel, status, created_at)
                        VALUES
                            (:id, :jid, :tname, :deps::jsonb, :depth,
                             :order, :parallel, 'pending', :now)
                    """),
                    {
                        "id":       str(uuid.uuid4()),
                        "jid":      job_id,
                        "tname":    tname,
                        "deps":     json.dumps(node.depends_on),
                        "depth":    depth,
                        "order":    exec_order,
                        "parallel": node.can_parallel,
                        "now":      datetime.datetime.utcnow(),
                    }
                )
                exec_order += 1

        # Also update migration_tables with depth and order info
        for depth, tables in levels.items():
            for i, tname in enumerate(tables):
                db.execute(
                    text("""
                        UPDATE migration_tables
                        SET depth_level = :depth, execution_order = :order,
                            depends_on = :deps::jsonb
                        WHERE job_id = :jid AND table_name = :tname
                    """),
                    {
                        "depth": depth,
                        "order": depth * 1000 + i,
                        "deps":  json.dumps(nodes.get(tname, TableNode(table_name=tname)).depends_on),
                        "jid":   job_id,
                        "tname": tname,
                    }
                )

        db.execute(
            text("UPDATE migration_jobs SET dependency_graph_built = TRUE WHERE id = :jid"),
            {"jid": job_id}
        )

        try:
            db.commit()
        except Exception as e:
            db.rollback()
            logger.error("Failed to save dependency graph", error=str(e))
