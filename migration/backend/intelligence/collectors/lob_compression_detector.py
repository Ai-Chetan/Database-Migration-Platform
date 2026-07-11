"""
LOB & Compression Detector
File: migration/backend/intelligence/collectors/lob_compression_detector.py

Detects:
  1. Large Object (LOB) columns — BLOB, TEXT, CLOB, BYTEA columns that
     may need special handling (streaming, chunking differently, GridFS
     for MongoDB in Part 15, separate tablespace in PostgreSQL)

  2. Table compression — whether the source table uses compression
     (MySQL InnoDB compression, PostgreSQL TOAST, etc.)
     Affects size estimates: a "5TB table" compressed at 4:1 is actually
     20TB uncompressed — the Cost Estimator needs to know this.

catalog_type = "lob_detection"
data shape:
{
    "has_lob":              true,
    "lob_columns":          ["document_blob", "raw_data", "notes"],
    "lob_column_details":   {
        "document_blob": {
            "type":              "longblob",
            "avg_size_bytes":    45000,
            "max_size_bytes":    10485760,   -- 10MB
            "total_size_bytes":  450000000,
            "null_pct":          12.0,
            "recommendation":    "stream_separately"
        }
    },
    "estimated_lob_size_bytes": 450000000,
    "lob_pct_of_table_size":    15.5,
    "recommendation":           "Use streaming for LOB columns. Consider separate chunk strategy."
}

catalog_type = "compression"
data shape:
{
    "is_compressed":         true,
    "compression_method":    "InnoDB ROW_FORMAT=COMPRESSED",
    "compression_ratio":     3.2,
    "compressed_size_bytes": 900000000,
    "estimated_uncompressed_bytes": 2880000000,
    "note": "Target may require more storage than source size suggests"
}
"""

import datetime
from typing import Dict, Any, List, Optional
from sqlalchemy.orm import Session

from backend.connector_framework.registry.connector_registry import ConnectorRegistry
from backend.kernel.metadata_catalog.metadata_catalog import MetadataCatalog
from backend.shared.config.logging import logger


LOB_TYPES = {
    "mysql":      {"blob","tinyblob","mediumblob","longblob","text","tinytext",
                   "mediumtext","longtext","json"},
    "postgresql": {"bytea","text","json","jsonb","xml","oid"},
}


class LOBCompressionDetector:

    TTL_HOURS = 72

    def collect_table(
        self,
        db:            Session,
        connection_id: str,
        source_config: dict,
        table_name:    str,
        schema_info:   Dict[str, Any],
        tenant_id:     str = "local",
    ) -> Dict[str, Any]:
        """Detect LOBs and compression for one table."""
        engine    = source_config.get("engine", "mysql").lower()
        table_def = schema_info.get("tables", {}).get(table_name, {})
        columns   = table_def.get("columns", {})

        lob_result  = self._detect_lobs(engine, table_name, columns, source_config)
        comp_result = self._detect_compression(engine, table_name, source_config)

        MetadataCatalog.write(
            db=db, table_name=table_name, catalog_type="lob_detection",
            data=lob_result, connection_id=connection_id,
            tenant_id=tenant_id, ttl_hours=self.TTL_HOURS,
        )
        MetadataCatalog.write(
            db=db, table_name=table_name, catalog_type="compression",
            data=comp_result, connection_id=connection_id,
            tenant_id=tenant_id, ttl_hours=self.TTL_HOURS,
        )

        logger.info("LOB/Compression detection complete",
                    table=table_name,
                    has_lob=lob_result.get("has_lob"),
                    is_compressed=comp_result.get("is_compressed"))

        return {"lob": lob_result, "compression": comp_result}

    def collect_all(
        self,
        db:            Session,
        connection_id: str,
        source_config: dict,
        table_names:   List[str],
        schema_info:   Dict[str, Any],
        tenant_id:     str = "local",
    ) -> Dict[str, Dict]:
        results = {}
        for table_name in table_names:
            try:
                results[table_name] = self.collect_table(
                    db=db, connection_id=connection_id,
                    source_config=source_config,
                    table_name=table_name, schema_info=schema_info,
                    tenant_id=tenant_id,
                )
            except Exception as e:
                logger.warning("LOB/Compression detection failed",
                               table=table_name, error=str(e))
                results[table_name] = {"error": str(e)}
        return results

    # ── Private ───────────────────────────────────────────────────────────────

    def _detect_lobs(self, engine, table_name, columns, source_config) -> Dict[str, Any]:
        lob_type_set  = LOB_TYPES.get(engine, set())
        lob_cols      = []
        lob_details   = {}

        for col_name, col_def in columns.items():
            base = col_def.get("type", "").split("(")[0].lower()
            if base in lob_type_set:
                lob_cols.append(col_name)

        if not lob_cols:
            return {
                "has_lob": False,
                "lob_columns": [],
                "lob_column_details": {},
                "estimated_lob_size_bytes": 0,
                "recommendation": "No LOB columns detected",
            }

        # Sample LOB sizes for each LOB column
        total_lob_bytes = 0
        connector = ConnectorRegistry.get_for_config(source_config)
        connector.connect()

        try:
            conn   = connector._connection
            cursor = conn.cursor()
            q      = (lambda col: f"`{col}`") if engine == "mysql" else (lambda col: f'"{col}"')

            for col_name in lob_cols:
                try:
                    if engine == "mysql":
                        cursor.execute(
                            f"SELECT AVG(LENGTH({q(col_name)})), "
                            f"MAX(LENGTH({q(col_name)})), "
                            f"SUM(LENGTH({q(col_name)})), "
                            f"SUM(CASE WHEN {q(col_name)} IS NULL THEN 1 ELSE 0 END) * 100.0 / COUNT(*) "
                            f"FROM {q(table_name)}"
                        )
                    else:
                        cursor.execute(
                            f"SELECT AVG(LENGTH({q(col_name)}::TEXT)), "
                            f"MAX(LENGTH({q(col_name)}::TEXT)), "
                            f"SUM(LENGTH({q(col_name)}::TEXT)), "
                            f"SUM(CASE WHEN {q(col_name)} IS NULL THEN 1 ELSE 0 END) * 100.0 / COUNT(*) "
                            f'FROM {q(table_name)}'
                        )
                    row = cursor.fetchone()
                    if row:
                        avg_bytes   = int(row[0] or 0)
                        max_bytes   = int(row[1] or 0)
                        total_bytes = int(row[2] or 0)
                        null_pct    = float(row[3] or 0.0)
                        total_lob_bytes += total_bytes

                        rec = "stream_separately" if max_bytes > 1_000_000 else "inline_ok"

                        lob_details[col_name] = {
                            "type":             columns[col_name].get("type", ""),
                            "avg_size_bytes":   avg_bytes,
                            "max_size_bytes":   max_bytes,
                            "total_size_bytes": total_bytes,
                            "null_pct":         round(null_pct, 2),
                            "recommendation":   rec,
                        }
                except Exception as e:
                    lob_details[col_name] = {"error": str(e)}

            cursor.close()
        finally:
            connector.disconnect()

        rec = (
            "Use streaming for LOB columns. Reduce chunk size to avoid OOM."
            if total_lob_bytes > 100_000_000 else
            "LOB columns present but manageable. Monitor memory during migration."
        )

        return {
            "has_lob":                 True,
            "lob_columns":             lob_cols,
            "lob_column_details":      lob_details,
            "estimated_lob_size_bytes": total_lob_bytes,
            "recommendation":          rec,
        }

    def _detect_compression(self, engine, table_name, source_config) -> Dict[str, Any]:
        connector = ConnectorRegistry.get_for_config(source_config)
        connector.connect()

        try:
            conn   = connector._connection
            cursor = conn.cursor()

            if engine == "mysql":
                cursor.execute(
                    "SELECT ROW_FORMAT, DATA_LENGTH, DATA_FREE "
                    "FROM information_schema.TABLES "
                    "WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s",
                    (source_config.get("database"), table_name)
                )
                row = cursor.fetchone()
                cursor.close()

                if row:
                    row_format   = row[0] or ""
                    data_length  = int(row[1] or 0)
                    is_compressed = row_format.upper() in ("COMPRESSED", "DYNAMIC")

                    if is_compressed:
                        estimated_ratio = 3.0   # typical InnoDB compression ratio
                        return {
                            "is_compressed":              is_compressed,
                            "compression_method":         f"InnoDB ROW_FORMAT={row_format}",
                            "compression_ratio":          estimated_ratio,
                            "compressed_size_bytes":      data_length,
                            "estimated_uncompressed_bytes": int(data_length * estimated_ratio),
                            "note": "Target may require more storage than source size suggests",
                        }

            elif engine == "postgresql":
                # PostgreSQL uses TOAST for large values — always active
                cursor.execute(
                    "SELECT pg_total_relation_size(%s)::BIGINT, "
                    "pg_relation_size(%s)::BIGINT",
                    (table_name, table_name)
                )
                row = cursor.fetchone()
                cursor.close()

                if row:
                    total_size = int(row[0] or 0)
                    heap_size  = int(row[1] or 0)
                    toast_size = total_size - heap_size

                    if toast_size > 0:
                        return {
                            "is_compressed":    True,
                            "compression_method": "PostgreSQL TOAST",
                            "toast_size_bytes": toast_size,
                            "heap_size_bytes":  heap_size,
                            "note": "TOAST data is transparent — no special handling needed",
                        }

        except Exception as e:
            logger.debug("Compression detection failed", table=table_name, error=str(e))
        finally:
            connector.disconnect()

        return {
            "is_compressed":   False,
            "compression_method": None,
            "note": "No compression detected",
        }
