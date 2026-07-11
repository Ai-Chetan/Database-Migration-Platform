"""
Distribution Analyzer
File: migration/backend/intelligence/collectors/distribution_analyzer.py

Analyzes data distribution per column — skew, top values, NULL rates.
This feeds the Adaptive Chunk Planner (avoids creating chunks that are
all empty because 80% of rows have the same status='deleted') and the
Assessment Engine (flags highly skewed distributions as migration risks).

catalog_type = "distribution"
data shape per table:
{
    "table_name": "orders",
    "columns": {
        "status": {
            "null_pct":         0.0,
            "distinct_count":   4,
            "distinct_pct":     0.0001,
            "top_values":       [{"value": "completed", "count": 8000000, "pct": 80.0},
                                 {"value": "pending",   "count": 1500000, "pct": 15.0},
                                 {"value": "failed",    "count": 400000,  "pct": 4.0},
                                 {"value": "cancelled", "count": 100000,  "pct": 1.0}],
            "is_skewed":        true,    -- top value > 50% of rows
            "skew_ratio":       80.0,    -- % held by the single most common value
            "data_type_class":  "categorical"
        },
        "amount": {
            "null_pct":         2.1,
            "distinct_count":   982341,
            "min_val":          "0.01",
            "max_val":          "99999.99",
            "avg_val":          "127.43",
            "stddev_val":       "284.12",
            "data_type_class":  "numeric"
        }
    },
    "skewed_columns":   ["status"],
    "high_null_columns": []
}
"""

import datetime
from typing import Dict, Any, List, Optional
from sqlalchemy.orm import Session

from backend.connector_framework.registry.connector_registry import ConnectorRegistry
from backend.kernel.metadata_catalog.metadata_catalog import MetadataCatalog
from backend.shared.config.logging import logger


TOP_N_VALUES = 10      # How many top values to store per column
MAX_COLUMNS  = 50      # Max columns to profile per table (avoid huge tables)
SKEW_THRESHOLD = 50.0  # % — column is "skewed" if top value holds > this % of rows


class DistributionAnalyzer:

    TTL_HOURS = 48

    def collect_table(
        self,
        db:            Session,
        connection_id: str,
        source_config: dict,
        table_name:    str,
        schema_info:   Dict[str, Any],
        tenant_id:     str = "local",
        sample_limit:  int = 1_000_000,  # analyze up to 1M rows, not full table
    ) -> Dict[str, Any]:
        """
        Analyze column distributions for one table.
        Uses database-side aggregations — never pulls rows to app memory.
        """
        engine    = source_config.get("engine", "mysql").lower()
        table_def = schema_info.get("tables", {}).get(table_name, {})
        columns   = table_def.get("columns", {})

        if not columns:
            return {}

        connector = ConnectorRegistry.get_for_config(source_config)
        connector.connect()

        col_profiles  = {}
        skewed_cols   = []
        high_null_cols = []

        try:
            conn   = connector._connection
            cursor = conn.cursor()
            q      = self._q(engine)

            # Limit to MAX_COLUMNS columns, prioritise non-PK, non-blob columns
            cols_to_profile = [
                col for col, defn in list(columns.items())[:MAX_COLUMNS]
                if not self._is_blob_type(defn.get("type", ""))
            ]

            total_rows = connector.get_row_count(table_name)

            for col_name in cols_to_profile:
                col_def    = columns[col_name]
                type_class = self._type_class(col_def.get("type", ""))

                profile = {"null_pct": 0.0, "data_type_class": type_class}

                try:
                    # NULL percentage
                    cursor.execute(
                        f"SELECT COUNT(*) FROM {q(table_name)} "
                        f"WHERE {q(col_name)} IS NULL"
                    )
                    null_count = cursor.fetchone()[0] or 0
                    null_pct   = round(null_count / total_rows * 100, 2) if total_rows > 0 else 0.0
                    profile["null_pct"] = null_pct

                    if null_pct >= 20.0:
                        high_null_cols.append(col_name)

                    # Distinct count
                    cursor.execute(
                        f"SELECT COUNT(DISTINCT {q(col_name)}) FROM {q(table_name)}"
                    )
                    distinct = cursor.fetchone()[0] or 0
                    profile["distinct_count"] = distinct
                    profile["distinct_pct"] = round(distinct / total_rows * 100, 4) if total_rows > 0 else 0

                    if type_class == "categorical" or distinct <= 1000:
                        # Top N values for low-cardinality / categorical columns
                        cursor.execute(
                            f"SELECT {q(col_name)}, COUNT(*) AS cnt "
                            f"FROM {q(table_name)} "
                            f"WHERE {q(col_name)} IS NOT NULL "
                            f"GROUP BY {q(col_name)} "
                            f"ORDER BY cnt DESC "
                            f"LIMIT {TOP_N_VALUES}"
                        )
                        top_rows   = cursor.fetchall()
                        top_values = []
                        for tv_row in top_rows:
                            val, cnt = tv_row[0], tv_row[1]
                            pct = round(cnt / total_rows * 100, 2) if total_rows > 0 else 0
                            top_values.append({"value": str(val), "count": cnt, "pct": pct})

                        profile["top_values"] = top_values

                        if top_values:
                            top_pct = top_values[0]["pct"]
                            profile["is_skewed"]   = top_pct >= SKEW_THRESHOLD
                            profile["skew_ratio"]  = top_pct
                            if profile["is_skewed"]:
                                skewed_cols.append(col_name)

                    elif type_class == "numeric":
                        # Min/max/avg/stddev for numeric columns
                        if engine == "mysql":
                            cursor.execute(
                                f"SELECT MIN({q(col_name)}), MAX({q(col_name)}), "
                                f"AVG({q(col_name)}), STDDEV({q(col_name)}) "
                                f"FROM {q(table_name)}"
                            )
                        else:
                            cursor.execute(
                                f"SELECT MIN({q(col_name)}), MAX({q(col_name)}), "
                                f"AVG({q(col_name)}), STDDEV({q(col_name)}) "
                                f"FROM {q(table_name)}"
                            )
                        stat_row = cursor.fetchone()
                        if stat_row:
                            profile["min_val"]    = str(stat_row[0]) if stat_row[0] is not None else None
                            profile["max_val"]    = str(stat_row[1]) if stat_row[1] is not None else None
                            profile["avg_val"]    = str(round(float(stat_row[2]), 4)) if stat_row[2] is not None else None
                            profile["stddev_val"] = str(round(float(stat_row[3]), 4)) if stat_row[3] is not None else None

                except Exception as col_err:
                    logger.debug("Column distribution failed",
                                 table=table_name, col=col_name, error=str(col_err))
                    profile["error"] = str(col_err)

                col_profiles[col_name] = profile

            cursor.close()

        finally:
            connector.disconnect()

        result = {
            "table_name":        table_name,
            "total_rows":        total_rows,
            "columns_analyzed":  len(col_profiles),
            "columns":           col_profiles,
            "skewed_columns":    skewed_cols,
            "high_null_columns": high_null_cols,
        }

        MetadataCatalog.write(
            db=db,
            table_name=table_name,
            catalog_type="distribution",
            data=result,
            connection_id=connection_id,
            tenant_id=tenant_id,
            ttl_hours=self.TTL_HOURS,
        )

        logger.info("Distribution analyzed",
                    table=table_name, cols=len(col_profiles),
                    skewed=len(skewed_cols))
        return result

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
                logger.warning("Distribution analysis failed",
                               table=table_name, error=str(e))
                results[table_name] = {"error": str(e)}
        return results

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _type_class(self, sql_type: str) -> str:
        """Classify SQL type into: numeric | categorical | datetime | binary | text."""
        base = sql_type.split("(")[0].lower()
        numeric  = {"int","integer","bigint","smallint","tinyint","mediumint",
                    "float","double","decimal","numeric","real","serial","bigserial"}
        datetime = {"date","datetime","timestamp","timestamptz","time","year"}
        binary   = {"blob","tinyblob","mediumblob","longblob","binary","varbinary","bytea"}
        text     = {"text","tinytext","mediumtext","longtext","clob"}

        if base in numeric:   return "numeric"
        if base in datetime:  return "datetime"
        if base in binary:    return "binary"
        if base in text:      return "text"
        return "categorical"   # varchar, char, enum, etc.

    def _is_blob_type(self, sql_type: str) -> bool:
        base = sql_type.split("(")[0].lower()
        return base in {"blob","tinyblob","mediumblob","longblob","binary","varbinary","bytea"}

    def _q(self, engine: str):
        if engine == "mysql":
            return lambda col: f"`{col}`"
        return lambda col: f'"{col}"'
