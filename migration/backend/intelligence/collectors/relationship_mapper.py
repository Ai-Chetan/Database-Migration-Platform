"""
Relationship Mapper
File: migration/backend/intelligence/collectors/relationship_mapper.py

Analyzes FK relationships and computes actual cardinality (1:1, 1:N, N:M)
by sampling data — not just reading the FK definition.

Why this matters:
    The schema says orders.customer_id FK → customers.id
    But what's the actual cardinality?
    - If one customer has millions of orders: 1:N (high fan-out)
    - If average is 1.2 orders per customer: almost 1:1
    This changes the Advisor's embed-vs-reference recommendation (Part 15)
    and affects chunk planning (high-fan-out child tables need smaller chunks).

catalog_type = "relationship"
data shape per FK:
{
    "source_table":     "orders",
    "source_column":    "customer_id",
    "target_table":     "customers",
    "target_column":    "id",
    "constraint_name":  "fk_orders_customer",
    "cardinality":      "1:N",
    "avg_children_per_parent": 8.4,
    "max_children_per_parent": 1240,
    "min_children_per_parent": 1,
    "orphan_count":     0,        -- rows where FK value has no parent
    "orphan_pct":       0.0,
    "nullable_fk":      false,
    "sampled_rows":     10000
}
"""

import datetime
from typing import Dict, Any, List, Optional
from sqlalchemy.orm import Session

from backend.connector_framework.registry.connector_registry import ConnectorRegistry
from backend.kernel.metadata_catalog.metadata_catalog import MetadataCatalog
from backend.shared.config.logging import logger


SAMPLE_SIZE = 10_000   # rows to sample for cardinality estimation


class RelationshipMapper:

    TTL_HOURS = 48

    def collect_table(
        self,
        db:            Session,
        connection_id: str,
        source_config: dict,
        table_name:    str,
        schema_info:   Dict[str, Any],  # full schema from connector.discover_schema()
        tenant_id:     str = "local",
    ) -> List[Dict[str, Any]]:
        """
        Analyze all FK relationships for one table.
        Returns list of relationship dicts (one per FK).
        """
        tables      = schema_info.get("tables", {})
        table_def   = tables.get(table_name, {})
        foreign_keys = table_def.get("foreign_keys", [])

        if not foreign_keys:
            return []

        results = []
        connector = ConnectorRegistry.get_for_config(source_config)
        connector.connect()

        try:
            for fk in foreign_keys:
                rel = self._analyze_fk(
                    connector=connector,
                    source_table=table_name,
                    source_col=fk["column"],
                    target_table=fk["ref_table"],
                    target_col=fk["ref_column"],
                    constraint_name=fk.get("constraint_name", ""),
                    engine=source_config.get("engine", "mysql"),
                    nullable=table_def.get("columns", {}).get(fk["column"], {}).get("nullable", True),
                )
                results.append(rel)

                # Write one catalog entry per FK relationship
                MetadataCatalog.write(
                    db=db,
                    table_name=table_name,
                    catalog_type="relationship",
                    data=rel,
                    connection_id=connection_id,
                    tenant_id=tenant_id,
                    ttl_hours=self.TTL_HOURS,
                )

        finally:
            connector.disconnect()

        logger.info("Relationships analyzed",
                    table=table_name, fk_count=len(results))
        return results

    def collect_all(
        self,
        db:            Session,
        connection_id: str,
        source_config: dict,
        table_names:   List[str],
        schema_info:   Dict[str, Any],
        tenant_id:     str = "local",
    ) -> Dict[str, List[Dict]]:
        """Analyze relationships for all tables. Returns {table_name: [rel_dicts]}."""
        results = {}
        for table_name in table_names:
            try:
                rels = self.collect_table(
                    db=db,
                    connection_id=connection_id,
                    source_config=source_config,
                    table_name=table_name,
                    schema_info=schema_info,
                    tenant_id=tenant_id,
                )
                results[table_name] = rels
            except Exception as e:
                logger.warning("Relationship mapping failed",
                               table=table_name, error=str(e))
                results[table_name] = []
        return results

    # ── Private ───────────────────────────────────────────────────────────────

    def _analyze_fk(
        self,
        connector,
        source_table:    str,
        source_col:      str,
        target_table:    str,
        target_col:      str,
        constraint_name: str,
        engine:          str,
        nullable:        bool,
    ) -> Dict[str, Any]:
        conn   = connector._connection
        cursor = conn.cursor()

        result = {
            "source_table":    source_table,
            "source_column":   source_col,
            "target_table":    target_table,
            "target_column":   target_col,
            "constraint_name": constraint_name,
            "cardinality":     "unknown",
            "nullable_fk":     nullable,
            "sampled_rows":    0,
        }

        try:
            q = self._q(engine)

            # Count distinct FK values (parents referenced)
            cursor.execute(
                f"SELECT COUNT(DISTINCT {q(source_col)}) FROM {q(source_table)} "
                f"WHERE {q(source_col)} IS NOT NULL LIMIT 1"
            )
            distinct_fk_vals = cursor.fetchone()[0] or 0

            # Count total non-null FK rows
            cursor.execute(
                f"SELECT COUNT(*) FROM {q(source_table)} "
                f"WHERE {q(source_col)} IS NOT NULL"
            )
            total_with_fk = cursor.fetchone()[0] or 0

            # Count orphan rows (FK value with no parent)
            cursor.execute(
                f"SELECT COUNT(*) FROM {q(source_table)} s "
                f"LEFT JOIN {q(target_table)} t ON s.{q(source_col)} = t.{q(target_col)} "
                f"WHERE s.{q(source_col)} IS NOT NULL AND t.{q(target_col)} IS NULL"
            )
            orphan_count = cursor.fetchone()[0] or 0

            # Sample to get avg/max children per parent
            avg_children = 0.0
            max_children = 0
            min_children = 0

            if distinct_fk_vals > 0 and total_with_fk > 0:
                avg_children = round(total_with_fk / distinct_fk_vals, 2)

                # Get max children per parent via GROUP BY
                try:
                    cursor.execute(
                        f"SELECT MAX(cnt) FROM ("
                        f"  SELECT COUNT(*) AS cnt FROM {q(source_table)} "
                        f"  WHERE {q(source_col)} IS NOT NULL "
                        f"  GROUP BY {q(source_col)} LIMIT {SAMPLE_SIZE}"
                        f") sub"
                    )
                    row = cursor.fetchone()
                    max_children = int(row[0] or 0)

                    cursor.execute(
                        f"SELECT MIN(cnt) FROM ("
                        f"  SELECT COUNT(*) AS cnt FROM {q(source_table)} "
                        f"  WHERE {q(source_col)} IS NOT NULL "
                        f"  GROUP BY {q(source_col)} LIMIT {SAMPLE_SIZE}"
                        f") sub"
                    )
                    row = cursor.fetchone()
                    min_children = int(row[0] or 0)
                except Exception:
                    pass

            # Determine cardinality
            if avg_children <= 1.05 and max_children <= 1:
                cardinality = "1:1"
            elif avg_children <= 3 and max_children <= 10:
                cardinality = "1:N (low)"
            elif avg_children <= 50:
                cardinality = "1:N"
            else:
                cardinality = "1:N (high fan-out)"

            result.update({
                "cardinality":              cardinality,
                "avg_children_per_parent":  avg_children,
                "max_children_per_parent":  max_children,
                "min_children_per_parent":  min_children,
                "orphan_count":             orphan_count,
                "orphan_pct":               round(orphan_count / total_with_fk * 100, 2)
                                            if total_with_fk > 0 else 0.0,
                "sampled_rows":             min(total_with_fk, SAMPLE_SIZE),
                "distinct_parent_values":   distinct_fk_vals,
            })

        except Exception as e:
            logger.warning("FK cardinality analysis failed",
                           table=source_table, fk=source_col, error=str(e))
            result["error"] = str(e)
        finally:
            cursor.close()

        return result

    def _q(self, engine: str):
        """Return quoting function for the engine."""
        if engine == "mysql":
            return lambda col: f"`{col}`"
        return lambda col: f'"{col}"'
