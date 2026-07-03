"""
Scan Orchestrator
File: migration/backend/intelligence/analyzers/scan_orchestrator.py

Coordinates all four collectors into one full intelligence scan.
A scan runs in this order:
    1. Statistics Collector     (row counts, sizes, PK stats, growth rates)
    2. Relationship Mapper      (FK cardinality analysis)
    3. Distribution Analyzer    (column distributions, skew, NULL rates)
    4. LOB/Compression Detector (large objects, compression)

All results flow into the Metadata Catalog (Part 1).

The orchestrator:
    - Creates and tracks an intelligence_scan_jobs record
    - Publishes events to the Event Bus (scan.started, scan.completed, scan.failed)
    - Updates progress as each table completes
    - Can be called synchronously (for small schemas) or kicked off in a
      background thread (for large schemas — the API returns scan_job_id
      immediately and the caller polls GET /intelligence/scans/{id})

Usage:
    orchestrator = ScanOrchestrator()
    result = orchestrator.run(
        db=db,
        connection_id="abc-123",
        source_config={...},
        tenant_id="local",
    )
"""

import uuid
import datetime
import json
import threading
from typing import Dict, Any, List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import text

from backend.intelligence.collectors.statistics_collector import StatisticsCollector
from backend.intelligence.collectors.relationship_mapper import RelationshipMapper
from backend.intelligence.collectors.distribution_analyzer import DistributionAnalyzer
from backend.intelligence.collectors.lob_compression_detector import LOBCompressionDetector
from backend.connector_framework.registry.connector_registry import ConnectorRegistry
from backend.shared.config.logging import logger


class ScanOrchestrator:

    def __init__(self):
        self.stats_collector  = StatisticsCollector()
        self.rel_mapper       = RelationshipMapper()
        self.dist_analyzer    = DistributionAnalyzer()
        self.lob_detector     = LOBCompressionDetector()

    def run(
        self,
        db:              Session,
        connection_id:   str,
        source_config:   dict,
        tenant_id:       str = "local",
        table_names:     Optional[List[str]] = None,   # None = scan all tables
        skip_types:      Optional[List[str]] = None,   # e.g. ["distribution"] to skip
        background:      bool = False,
    ) -> Dict[str, Any]:
        """
        Run the full intelligence scan. Creates a scan_job record, runs all
        collectors, writes to Metadata Catalog.

        background=True: returns {scan_job_id, status: "running"} immediately
                         and runs the scan in a daemon thread.
        background=False: blocks until complete, returns full summary.
        """
        skip_types = skip_types or []
        scan_job_id = str(uuid.uuid4())

        # ── Discover schema first ─────────────────────────────────────────
        try:
            connector = ConnectorRegistry.get_for_config(source_config)
            with connector:
                schema_info  = connector.discover_schema()
                schema_tables = schema_info.tables
        except Exception as e:
            logger.error("Schema discovery failed during scan", error=str(e))
            return {"error": f"Schema discovery failed: {e}", "scan_job_id": scan_job_id}

        tables_to_scan = table_names or list(schema_tables.keys())

        # ── Create scan job record ────────────────────────────────────────
        self._create_scan_job(db, scan_job_id, connection_id, tenant_id,
                              len(tables_to_scan))
        self._publish_event("scan.started", scan_job_id, connection_id, {
            "tables": len(tables_to_scan),
            "skip_types": skip_types,
        })

        if background:
            # Launch in background thread, return immediately
            def _bg():
                from backend.shared.config.database import SessionLocal
                bg_db = SessionLocal()
                try:
                    self._run_all_collectors(
                        db=bg_db,
                        scan_job_id=scan_job_id,
                        connection_id=connection_id,
                        source_config=source_config,
                        tables_to_scan=tables_to_scan,
                        schema_info={"tables": {k: v.__dict__ if hasattr(v, '__dict__') else v
                                                for k, v in schema_tables.items()}},
                        skip_types=skip_types,
                        tenant_id=tenant_id,
                    )
                finally:
                    bg_db.close()

            thread = threading.Thread(target=_bg, daemon=True,
                                      name=f"scan-{scan_job_id[:8]}")
            thread.start()
            return {"scan_job_id": scan_job_id, "status": "running",
                    "tables": len(tables_to_scan),
                    "message": f"Scan started in background. Poll GET /intelligence/scans/{scan_job_id}"}

        # Synchronous run
        schema_dict = {"tables": {k: v.__dict__ if hasattr(v, '__dict__') else v
                                  for k, v in schema_tables.items()}}
        return self._run_all_collectors(
            db=db,
            scan_job_id=scan_job_id,
            connection_id=connection_id,
            source_config=source_config,
            tables_to_scan=tables_to_scan,
            schema_info=schema_dict,
            skip_types=skip_types,
            tenant_id=tenant_id,
        )

    def _run_all_collectors(
        self,
        db:              Session,
        scan_job_id:     str,
        connection_id:   str,
        source_config:   dict,
        tables_to_scan:  List[str],
        schema_info:     Dict[str, Any],
        skip_types:      List[str],
        tenant_id:       str,
    ) -> Dict[str, Any]:
        """Inner method: runs all collectors. Called both sync and from bg thread."""

        self._update_scan_status(db, scan_job_id, "running",
                                 started_at=datetime.datetime.utcnow())

        table_results = []
        total_failed  = 0

        # PK columns from schema_info
        pk_columns = {}
        for tname, tdata in schema_info.get("tables", {}).items():
            pks = tdata.get("primary_keys", [])
            pk_columns[tname] = pks[0] if pks else "id"

        # ── 1. Statistics (always runs) ───────────────────────────────────
        logger.info("Scan phase 1/4: Statistics", scan_job_id=scan_job_id)
        for i, table_name in enumerate(tables_to_scan):
            completed_types = []
            errors          = []
            try:
                self.stats_collector.collect_table(
                    db=db, connection_id=connection_id,
                    source_config=source_config,
                    table_name=table_name,
                    pk_column=pk_columns.get(table_name, "id"),
                    tenant_id=tenant_id,
                )
                completed_types.append("statistics")
                completed_types.append("growth_rate")
            except Exception as e:
                errors.append(f"statistics: {str(e)[:100]}")
                total_failed += 1

            # ── 2. Relationship Mapper ─────────────────────────────────────
            if "relationship" not in skip_types:
                try:
                    self.rel_mapper.collect_table(
                        db=db, connection_id=connection_id,
                        source_config=source_config,
                        table_name=table_name,
                        schema_info=schema_info,
                        tenant_id=tenant_id,
                    )
                    completed_types.append("relationship")
                except Exception as e:
                    errors.append(f"relationship: {str(e)[:100]}")

            # ── 3. Distribution Analyzer ───────────────────────────────────
            if "distribution" not in skip_types:
                try:
                    self.dist_analyzer.collect_table(
                        db=db, connection_id=connection_id,
                        source_config=source_config,
                        table_name=table_name,
                        schema_info=schema_info,
                        tenant_id=tenant_id,
                    )
                    completed_types.append("distribution")
                except Exception as e:
                    errors.append(f"distribution: {str(e)[:100]}")

            # ── 4. LOB/Compression Detector ────────────────────────────────
            if "lob_detection" not in skip_types:
                try:
                    self.lob_detector.collect_table(
                        db=db, connection_id=connection_id,
                        source_config=source_config,
                        table_name=table_name,
                        schema_info=schema_info,
                        tenant_id=tenant_id,
                    )
                    completed_types.append("lob_detection")
                    completed_types.append("compression")
                except Exception as e:
                    errors.append(f"lob_detection: {str(e)[:100]}")

            table_results.append({
                "table_name":     table_name,
                "status":         "failed" if errors else "completed",
                "catalog_types":  completed_types,
                "errors":         errors,
            })

            # Update progress every 10 tables
            if (i + 1) % 10 == 0 or (i + 1) == len(tables_to_scan):
                self._update_scan_progress(db, scan_job_id, i + 1,
                                           len(tables_to_scan), total_failed,
                                           table_results)

        # ── Finalize ──────────────────────────────────────────────────────
        final_status = "completed" if total_failed == 0 else "partial"
        self._update_scan_status(
            db, scan_job_id, final_status,
            completed_at=datetime.datetime.utcnow(),
            table_results=table_results,
        )

        self._publish_event("scan.completed", scan_job_id, connection_id, {
            "tables_scanned": len(tables_to_scan),
            "tables_failed":  total_failed,
            "status":         final_status,
        })

        logger.info("Intelligence scan complete",
                    scan_job_id=scan_job_id,
                    tables=len(tables_to_scan),
                    failed=total_failed,
                    status=final_status)

        return {
            "scan_job_id":    scan_job_id,
            "status":         final_status,
            "tables_scanned": len(tables_to_scan),
            "tables_failed":  total_failed,
            "table_results":  table_results,
        }

    # ── DB helpers ────────────────────────────────────────────────────────────

    def _create_scan_job(self, db, scan_job_id, connection_id, tenant_id, total):
        try:
            db.execute(
                text("""
                    INSERT INTO intelligence_scan_jobs
                        (id, tenant_id, connection_id, status,
                         tables_total, tables_scanned, tables_failed,
                         created_at, updated_at)
                    VALUES
                        (:id, :tid, :cid, 'pending',
                         :total, 0, 0, :now, :now)
                """),
                {"id": scan_job_id, "tid": tenant_id, "cid": connection_id,
                 "total": total, "now": datetime.datetime.utcnow()}
            )
            db.commit()
        except Exception as e:
            logger.warning("Failed to create scan job record", error=str(e))

    def _update_scan_status(self, db, scan_job_id, status,
                            started_at=None, completed_at=None, table_results=None):
        try:
            params = {"s": status, "now": datetime.datetime.utcnow(), "id": scan_job_id}
            set_parts = ["status=:s", "updated_at=:now"]
            if started_at:
                set_parts.append("started_at=:sat")
                params["sat"] = started_at
            if completed_at:
                set_parts.append("completed_at=:cat")
                params["cat"] = completed_at
            if table_results is not None:
                set_parts.append("table_results=:tr::jsonb")
                params["tr"] = json.dumps(table_results)
            db.execute(
                text(f"UPDATE intelligence_scan_jobs SET {', '.join(set_parts)} WHERE id=:id"),
                params
            )
            db.commit()
        except Exception as e:
            logger.warning("Failed to update scan status", error=str(e))

    def _update_scan_progress(self, db, scan_job_id, scanned, total, failed, table_results):
        try:
            db.execute(
                text("""
                    UPDATE intelligence_scan_jobs SET
                        tables_scanned=:s, tables_failed=:f,
                        table_results=:tr::jsonb, updated_at=:now
                    WHERE id=:id
                """),
                {"s": scanned, "f": failed,
                 "tr": json.dumps(table_results[-20:]),  # keep last 20 to avoid huge JSONB
                 "now": datetime.datetime.utcnow(), "id": scan_job_id}
            )
            db.commit()
        except Exception:
            pass

    def _publish_event(self, event_type, scan_job_id, connection_id, payload):
        try:
            from backend.kernel.event_bus.event_bus import EventBus
            from backend.shared.config.database import SessionLocal
            db = SessionLocal()
            try:
                EventBus.publish(
                    event_type=event_type,
                    source_service="intelligence_service",
                    resource_type="scan_job",
                    resource_id=scan_job_id,
                    payload=payload,
                    correlation_id=scan_job_id,
                    db=db,
                )
            finally:
                db.close()
        except Exception as e:
            logger.debug("Event publish failed (non-fatal)", error=str(e))
