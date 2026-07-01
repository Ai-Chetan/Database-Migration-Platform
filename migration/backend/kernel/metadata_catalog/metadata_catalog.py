"""
Metadata Catalog
File: migration/backend/kernel/metadata_catalog/metadata_catalog.py

The persistent store the Metadata Intelligence Layer (Part 3) will write to,
and everything downstream — Assessment Engine, Migration Advisor, Cost
Estimator, Adaptive Chunk Planner, Workflow Engine, Simulation Engine — will
read from.

This file (Part 1) defines the CONTRACT: a generic write/read/query API over
catalog_type'd JSONB blobs per table. Part 3 will populate it with real
statistics/relationship/distribution/lob/compression data. Built now so the
storage shape and access pattern exist and is stable before Part 3's
collectors are written.

catalog_type values (Part 3 will produce these; this module just stores
and serves them generically — it does not know what's "inside" each type):

    statistics              {"row_count": N, "size_bytes": N, "avg_row_bytes": N,
                              "growth_rate_pct_per_month": N}
    relationship             {"cardinality": "1:N", "ref_table": "...", ...}
    distribution              {"column": "status", "skew_pct": 80, "top_value": "active"}
    lob_detection             {"has_lob": true, "lob_columns": ["doc_blob"], "lob_size_bytes": N}
    compression               {"is_compressed": true, "method": "..."}
    hot_cold_classification  {"classification": "hot", "read_freq": "high", "write_freq": "high"}
    growth_rate               {"rows_per_day": N, "projected_30d_rows": N}

Staleness:
    Each entry has computed_at and optional expires_at. get() and
    get_fresh() let callers decide whether to trust cached data or trigger
    a recompute (Part 3's job).

Usage:
    from backend.kernel.metadata_catalog.metadata_catalog import MetadataCatalog

    # Write (Part 3's collectors will call this)
    MetadataCatalog.write(
        db, connection_id=conn_id, table_name="orders",
        catalog_type="statistics",
        data={"row_count": 2_700_000_000, "size_bytes": 2_900_000_000_000,
              "avg_row_bytes": 1074},
        ttl_hours=24,
    )

    # Read (Assessment Engine, Advisor, Chunk Planner will call this)
    stats = MetadataCatalog.get(db, connection_id=conn_id, table_name="orders",
                                 catalog_type="statistics")
"""

import uuid
import json
import datetime
from typing import Optional, Dict, Any, List
from sqlalchemy.orm import Session
from sqlalchemy import text

from backend.shared.config.logging import logger


class MetadataCatalog:

    # ── Write ─────────────────────────────────────────────────────────────────

    @classmethod
    def write(
        cls,
        db:            Session,
        table_name:    str,
        catalog_type:  str,
        data:          Dict[str, Any],
        connection_id:    Optional[str] = None,
        schema_version_id: Optional[str] = None,
        tenant_id:     str = "local",
        ttl_hours:     Optional[int] = None,
    ) -> dict:
        """
        Write (upsert-by-insert) a catalog entry. Each write is a new row —
        history is kept naturally (query with ORDER BY computed_at DESC LIMIT 1
        for "latest", or query a time range for trend analysis e.g. growth rate).
        """
        entry_id = str(uuid.uuid4())
        now = datetime.datetime.utcnow()
        expires_at = now + datetime.timedelta(hours=ttl_hours) if ttl_hours else None

        db.execute(
            text("""
                INSERT INTO metadata_catalog
                    (id, tenant_id, connection_id, schema_version_id, table_name,
                     catalog_type, data, computed_at, expires_at, created_at)
                VALUES
                    (:id, :tid, :cid, :svid, :tname,
                     :ctype, :data::jsonb, :now, :expires, :now)
            """),
            {
                "id": entry_id, "tid": tenant_id, "cid": connection_id,
                "svid": schema_version_id, "tname": table_name,
                "ctype": catalog_type, "data": json.dumps(data),
                "now": now, "expires": expires_at,
            }
        )
        db.commit()
        logger.debug("Metadata catalog entry written",
                     table=table_name, catalog_type=catalog_type)
        return {"id": entry_id, "table_name": table_name, "catalog_type": catalog_type}

    @classmethod
    def write_bulk(
        cls,
        db:            Session,
        connection_id: str,
        entries:       List[Dict[str, Any]],
        tenant_id:     str = "local",
    ) -> int:
        """
        Bulk write multiple catalog entries in one call — used by Part 3's
        Statistics Collector after scanning every table in a schema.
        entries = [{"table_name": "...", "catalog_type": "...", "data": {...}, "ttl_hours": 24}, ...]
        """
        count = 0
        for entry in entries:
            cls.write(
                db=db,
                connection_id=connection_id,
                table_name=entry["table_name"],
                catalog_type=entry["catalog_type"],
                data=entry.get("data", {}),
                tenant_id=tenant_id,
                ttl_hours=entry.get("ttl_hours"),
            )
            count += 1
        return count

    # ── Read ──────────────────────────────────────────────────────────────────

    @classmethod
    def get(
        cls,
        db:            Session,
        table_name:    str,
        catalog_type:  str,
        connection_id: Optional[str] = None,
        only_fresh:    bool = False,
    ) -> Optional[Dict[str, Any]]:
        """
        Get the LATEST entry for (table_name, catalog_type[, connection_id]).
        If only_fresh=True, returns None if the latest entry has expired.
        """
        conditions = ["table_name = :tname", "catalog_type = :ctype"]
        params: Dict[str, Any] = {"tname": table_name, "ctype": catalog_type}

        if connection_id:
            conditions.append("connection_id = :cid")
            params["cid"] = connection_id
        if only_fresh:
            conditions.append("(expires_at IS NULL OR expires_at > NOW())")

        where = " AND ".join(conditions)

        row = db.execute(
            text(f"""
                SELECT data, computed_at, expires_at
                FROM metadata_catalog
                WHERE {where}
                ORDER BY computed_at DESC
                LIMIT 1
            """),
            params
        ).fetchone()

        if not row:
            return None

        data = row[0]
        if isinstance(data, str):
            data = json.loads(data)

        return {
            "data":        data,
            "computed_at": row[1].isoformat() if row[1] else None,
            "expires_at":  row[2].isoformat() if row[2] else None,
            "is_fresh":    row[2] is None or row[2] > datetime.datetime.utcnow(),
        }

    @classmethod
    def get_all_for_table(
        cls,
        db:            Session,
        table_name:    str,
        connection_id: Optional[str] = None,
    ) -> Dict[str, Dict[str, Any]]:
        """
        Get the latest entry of EVERY catalog_type for a table — the
        "give me everything we know about this table" call. This is exactly
        what the Assessment Engine (Part 4) will use to build its report.

        Returns: {"statistics": {...}, "relationship": {...}, "lob_detection": {...}, ...}
        """
        conditions = ["table_name = :tname"]
        params: Dict[str, Any] = {"tname": table_name}
        if connection_id:
            conditions.append("connection_id = :cid")
            params["cid"] = connection_id
        where = " AND ".join(conditions)

        rows = db.execute(
            text(f"""
                SELECT DISTINCT ON (catalog_type)
                    catalog_type, data, computed_at, expires_at
                FROM metadata_catalog
                WHERE {where}
                ORDER BY catalog_type, computed_at DESC
            """),
            params
        ).fetchall()

        result = {}
        for row in rows:
            data = row[1]
            if isinstance(data, str):
                data = json.loads(data)
            result[row[0]] = {
                "data":        data,
                "computed_at": row[2].isoformat() if row[2] else None,
                "is_fresh":    row[3] is None or row[3] > datetime.datetime.utcnow(),
            }
        return result

    @classmethod
    def get_history(
        cls,
        db:            Session,
        table_name:    str,
        catalog_type:  str,
        connection_id: Optional[str] = None,
        limit:         int = 30,
    ) -> List[Dict[str, Any]]:
        """
        Get historical entries for trend analysis — e.g. growth_rate over
        the last 30 snapshots. Part 6's Benchmark Engine and Part 3's
        growth-rate collector both rely on this.
        """
        conditions = ["table_name = :tname", "catalog_type = :ctype"]
        params: Dict[str, Any] = {"tname": table_name, "ctype": catalog_type, "lim": limit}
        if connection_id:
            conditions.append("connection_id = :cid")
            params["cid"] = connection_id
        where = " AND ".join(conditions)

        rows = db.execute(
            text(f"""
                SELECT data, computed_at FROM metadata_catalog
                WHERE {where} ORDER BY computed_at DESC LIMIT :lim
            """),
            params
        ).fetchall()

        result = []
        for row in rows:
            data = row[0]
            if isinstance(data, str):
                data = json.loads(data)
            result.append({"data": data, "computed_at": row[1].isoformat() if row[1] else None})
        return result

    @classmethod
    def list_stale(
        cls,
        db:            Session,
        connection_id: Optional[str] = None,
        catalog_type:  Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Find catalog entries that have expired and need recomputation.
        Part 3's collectors (or a scheduled job, once Part 9's Scheduler
        exists) will call this to know what to refresh.
        """
        conditions = ["expires_at IS NOT NULL", "expires_at <= NOW()"]
        params: Dict[str, Any] = {}
        if connection_id:
            conditions.append("connection_id = :cid")
            params["cid"] = connection_id
        if catalog_type:
            conditions.append("catalog_type = :ctype")
            params["ctype"] = catalog_type
        where = " AND ".join(conditions)

        rows = db.execute(
            text(f"""
                SELECT DISTINCT table_name, catalog_type, connection_id
                FROM metadata_catalog WHERE {where}
            """),
            params
        ).fetchall()

        return [
            {"table_name": r[0], "catalog_type": r[1],
             "connection_id": str(r[2]) if r[2] else None}
            for r in rows
        ]

    @classmethod
    def delete_for_table(
        cls,
        db:            Session,
        table_name:    str,
        connection_id: Optional[str] = None,
    ) -> int:
        """Delete all catalog entries for a table — used when a table is dropped/renamed."""
        conditions = ["table_name = :tname"]
        params: Dict[str, Any] = {"tname": table_name}
        if connection_id:
            conditions.append("connection_id = :cid")
            params["cid"] = connection_id
        where = " AND ".join(conditions)

        result = db.execute(text(f"DELETE FROM metadata_catalog WHERE {where}"), params)
        db.commit()
        return result.rowcount if hasattr(result, "rowcount") else 0
