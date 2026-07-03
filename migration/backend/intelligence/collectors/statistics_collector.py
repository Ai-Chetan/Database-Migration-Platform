"""
Statistics Collector
File: migration/backend/intelligence/collectors/statistics_collector.py

Collects per-table statistics and writes them to the Metadata Catalog.
This is the most fundamental collector — everything else (Assessment Engine,
Advisor, Cost Estimator, Adaptive Chunk Planner) reads from what this writes.

catalog_type = "statistics"
data shape:
{
    "row_count":              2_700_000_000,
    "size_bytes":             2_900_000_000_000,
    "size_gb":                2.7,
    "avg_row_bytes":          1074,
    "index_size_bytes":       340_000_000_000,
    "total_size_bytes":       3_240_000_000_000,
    "column_count":           24,
    "pk_column":              "order_id",
    "pk_min":                 1,
    "pk_max":                 2_900_000_000,
    "pk_fill_ratio":          0.93,       -- how dense the PK range is (1.0 = no gaps)
    "has_auto_increment":     true,
    "engine":                 "InnoDB",   -- MySQL only
    "charset":                "utf8mb4",  -- MySQL only
    "last_analyzed":          "2026-06-01T00:00:00"
}

catalog_type = "growth_rate"
data shape:
{
    "rows_at_scan":           2_700_000_000,
    "previous_row_count":     2_600_000_000,   -- from last scan (if available)
    "rows_added_since_last":  100_000_000,
    "days_since_last_scan":   30,
    "rows_per_day":           3_333_333,
    "rows_per_month":         100_000_000,
    "projected_rows_90d":     3_000_000_000,
    "projected_size_90d_gb":  3.1
}
"""

import datetime
import uuid
from typing import Dict, Any, Optional, List
from sqlalchemy.orm import Session

from backend.connector_framework.registry.connector_registry import ConnectorRegistry
from backend.kernel.metadata_catalog.metadata_catalog import MetadataCatalog
from backend.shared.config.logging import logger


class StatisticsCollector:

    TTL_HOURS = 24   # stats go stale after 24 hours

    def collect_table(
        self,
        db:            Session,
        connection_id: str,
        source_config: dict,
        table_name:    str,
        pk_column:     str = "id",
        tenant_id:     str = "local",
    ) -> Dict[str, Any]:
        """
        Collect statistics for one table. Returns the stats dict.
        Writes to metadata_catalog automatically.
        """
        engine = source_config.get("engine", "mysql").lower()
        stats  = {}

        try:
            connector = ConnectorRegistry.get_for_config(source_config)
            connector.connect()

            try:
                row_count    = connector.get_row_count(table_name)
                avg_row_bytes = connector.get_avg_row_size(table_name)
                size_bytes   = row_count * avg_row_bytes if row_count and avg_row_bytes else 0
                pk_min, pk_max, pk_fill = self._get_pk_stats(connector, table_name, pk_column, engine)
                index_bytes  = self._get_index_size(connector, table_name, engine, source_config)
                extra        = self._get_engine_extras(connector, table_name, engine, source_config)
            finally:
                connector.disconnect()

            stats = {
                "row_count":          row_count,
                "size_bytes":         size_bytes,
                "size_gb":            round(size_bytes / (1024**3), 3) if size_bytes else 0,
                "avg_row_bytes":      avg_row_bytes,
                "index_size_bytes":   index_bytes,
                "total_size_bytes":   size_bytes + index_bytes,
                "pk_column":          pk_column,
                "pk_min":             pk_min,
                "pk_max":             pk_max,
                "pk_fill_ratio":      pk_fill,
            }
            stats.update(extra)

            # Write to catalog
            MetadataCatalog.write(
                db=db,
                table_name=table_name,
                catalog_type="statistics",
                data=stats,
                connection_id=connection_id,
                tenant_id=tenant_id,
                ttl_hours=self.TTL_HOURS,
            )

            # Compute and write growth_rate if previous stats exist
            self._write_growth_rate(db, connection_id, table_name, row_count,
                                    size_bytes, avg_row_bytes, tenant_id)

            logger.info("Statistics collected",
                        table=table_name, rows=row_count,
                        size_gb=stats["size_gb"])

        except Exception as e:
            logger.error("Statistics collection failed",
                         table=table_name, error=str(e))
            stats["error"] = str(e)

        return stats

    def collect_all(
        self,
        db:            Session,
        connection_id: str,
        source_config: dict,
        table_names:   List[str],
        pk_columns:    Dict[str, str] = None,
        tenant_id:     str = "local",
        scan_job_id:   str = None,
    ) -> Dict[str, Dict[str, Any]]:
        """
        Collect statistics for all tables. Updates scan_job_id progress as it goes.
        Returns {table_name: stats_dict}.
        """
        pk_columns = pk_columns or {}
        results    = {}
        succeeded  = 0
        failed     = 0

        for i, table_name in enumerate(table_names):
            pk_col = pk_columns.get(table_name, "id")
            stats  = self.collect_table(
                db=db,
                connection_id=connection_id,
                source_config=source_config,
                table_name=table_name,
                pk_column=pk_col,
                tenant_id=tenant_id,
            )
            results[table_name] = stats

            if "error" in stats:
                failed += 1
            else:
                succeeded += 1

            # Update scan job progress
            if scan_job_id:
                self._update_scan_progress(db, scan_job_id, i + 1, len(table_names))

        logger.info("Statistics collection complete",
                    tables=len(table_names), succeeded=succeeded, failed=failed)
        return results

    # ── Private helpers ───────────────────────────────────────────────────────

    def _get_pk_stats(self, connector, table_name, pk_column, engine):
        """Get PK min, max, and fill ratio."""
        try:
            conn   = connector._connection
            cursor = conn.cursor()
            if engine == "mysql":
                cursor.execute(
                    f"SELECT MIN(`{pk_column}`), MAX(`{pk_column}`) FROM `{table_name}`"
                )
            else:
                cursor.execute(
                    f'SELECT MIN("{pk_column}"), MAX("{pk_column}") FROM "{table_name}"'
                )
            row = cursor.fetchone()
            cursor.close()

            if not row or row[0] is None:
                return None, None, None

            pk_min = row[0]
            pk_max = row[1]

            # Skip fill ratio for UUID PKs
            try:
                mn, mx = int(pk_min), int(pk_max)
                pk_range = mx - mn + 1
                row_count = connector.get_row_count(table_name)
                fill = round(row_count / pk_range, 4) if pk_range > 0 else 1.0
                return mn, mx, min(fill, 1.0)
            except (ValueError, TypeError):
                return str(pk_min), str(pk_max), None

        except Exception as e:
            logger.debug("PK stats failed", table=table_name, error=str(e))
            return None, None, None

    def _get_index_size(self, connector, table_name, engine, config) -> int:
        """Get total index size in bytes."""
        try:
            conn   = connector._connection
            cursor = conn.cursor()
            if engine == "mysql":
                cursor.execute(
                    "SELECT SUM(INDEX_LENGTH) FROM information_schema.TABLES "
                    "WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s",
                    (config.get("database"), table_name)
                )
                row = cursor.fetchone()
                cursor.close()
                return int(row[0] or 0)
            else:
                cursor.execute(
                    "SELECT pg_indexes_size(%s::regclass)::BIGINT", (table_name,)
                )
                row = cursor.fetchone()
                cursor.close()
                return int(row[0] or 0)
        except Exception:
            return 0

    def _get_engine_extras(self, connector, table_name, engine, config) -> dict:
        """MySQL/PG specific extras: engine, charset, encoding."""
        extras = {}
        try:
            conn   = connector._connection
            cursor = conn.cursor()
            if engine == "mysql":
                cursor.execute(
                    "SELECT ENGINE, TABLE_COLLATION, CREATE_OPTIONS, AUTO_INCREMENT "
                    "FROM information_schema.TABLES "
                    "WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s",
                    (config.get("database"), table_name)
                )
                row = cursor.fetchone()
                if row:
                    extras["storage_engine"]  = row[0]
                    extras["collation"]       = row[1]
                    extras["create_options"]  = row[2]
                    extras["has_auto_increment"] = row[3] is not None
            else:
                cursor.execute(
                    "SELECT pg_encoding_to_char(encoding) FROM pg_database "
                    "WHERE datname=current_database()"
                )
                row = cursor.fetchone()
                if row:
                    extras["encoding"] = row[0]
            cursor.close()
        except Exception:
            pass
        return extras

    def _write_growth_rate(
        self, db, connection_id, table_name, current_rows,
        current_bytes, avg_row_bytes, tenant_id
    ):
        """Compare against previous scan to compute growth rate."""
        try:
            previous = MetadataCatalog.get(
                db=db, table_name=table_name,
                catalog_type="statistics", connection_id=connection_id,
            )
            if not previous:
                return

            prev_data     = previous.get("data", {})
            prev_rows     = prev_data.get("row_count", 0)
            prev_computed = previous.get("computed_at")

            if not prev_rows or not prev_computed:
                return

            prev_dt = datetime.datetime.fromisoformat(prev_computed) if isinstance(prev_computed, str) else prev_computed
            days    = max((datetime.datetime.utcnow() - prev_dt).days, 1)
            added   = max(current_rows - prev_rows, 0)
            rpd     = added / days
            rpm     = rpd * 30
            rp90d   = current_rows + (rpd * 90)

            MetadataCatalog.write(
                db=db,
                table_name=table_name,
                catalog_type="growth_rate",
                data={
                    "rows_at_scan":          current_rows,
                    "previous_row_count":    prev_rows,
                    "rows_added_since_last": added,
                    "days_since_last_scan":  days,
                    "rows_per_day":          round(rpd, 2),
                    "rows_per_month":        round(rpm, 2),
                    "projected_rows_90d":    int(rp90d),
                    "projected_size_90d_gb": round((rp90d * avg_row_bytes) / (1024**3), 3),
                },
                connection_id=connection_id,
                tenant_id=tenant_id,
                ttl_hours=self.TTL_HOURS,
            )
        except Exception as e:
            logger.debug("Growth rate computation failed", table=table_name, error=str(e))

    def _update_scan_progress(self, db, scan_job_id, scanned, total):
        try:
            from sqlalchemy import text
            db.execute(
                text("UPDATE intelligence_scan_jobs SET tables_scanned=:s, updated_at=:now WHERE id=:id"),
                {"s": scanned, "now": datetime.datetime.utcnow(), "id": scan_job_id}
            )
            db.commit()
        except Exception:
            pass
