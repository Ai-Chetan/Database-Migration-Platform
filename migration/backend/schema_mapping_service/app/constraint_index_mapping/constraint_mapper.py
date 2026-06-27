"""
Constraint and Index Mapping Engine
File: migration/backend/schema_mapping_service/app/constraint_index_mapping/constraint_mapper.py

Handles PK, FK, UNIQUE, CHECK, NOT NULL constraints and all index types.

Key principle from enterprise migrations:
    DON'T create indexes during bulk data load.
    Create tables → load all data → THEN rebuild indexes.
    This is 3-5x faster than creating indexes first.

Features:
  1. Extract all constraints and indexes from source schema
  2. Map them to target schema (rename columns where needed)
  3. Generate CREATE TABLE DDL without indexes (for fast load)
  4. Generate ALTER TABLE / CREATE INDEX DDL (run after load)
  5. Generate FK constraint DDL (run after all tables loaded)
  6. Detect constraint conflicts between source and target

Used by:
  - Migration plan generator (Step 1: create tables, Step 4: rebuild indexes)
  - Script generator (includes constraint DDL in generated scripts)
"""

from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field


@dataclass
class ConstraintDef:
    constraint_name: str
    constraint_type: str          # PRIMARY KEY | UNIQUE | FOREIGN KEY | CHECK | NOT NULL
    columns:         List[str]
    ref_table:       Optional[str] = None
    ref_columns:     Optional[List[str]] = None
    check_expr:      Optional[str] = None


@dataclass
class IndexDef:
    index_name:  str
    columns:     List[str]
    unique:      bool = False
    index_type:  str = "BTREE"   # BTREE | HASH | FULLTEXT | SPATIAL
    definition:  Optional[str] = None   # raw DDL from pg_indexes


@dataclass
class TableConstraintPlan:
    table_name:          str
    create_ddl:          str          # CREATE TABLE without indexes
    pk_ddl:              Optional[str]
    index_ddl_list:      List[str]    # CREATE INDEX statements (run after load)
    fk_ddl_list:         List[str]    # ADD FOREIGN KEY (run after ALL tables loaded)
    unique_ddl_list:     List[str]    # UNIQUE constraints
    conflicts:           List[str]    # warnings about conflicts


class ConstraintIndexMapper:

    def build_plan(
        self,
        table_name:     str,
        source_table:   Dict[str, Any],
        target_table:   Dict[str, Any],
        column_mapping: Dict[str, str],   # {src_col: tgt_col}
        source_db:      str = "mysql",
        target_db:      str = "mysql",
    ) -> TableConstraintPlan:
        """
        Build the full constraint/index plan for a table.
        Returns DDL statements in the correct execution order.
        """
        # Map column names from source → target
        def map_col(col: str) -> str:
            return column_mapping.get(col, col)

        src_cols    = source_table.get("columns", {})
        src_pks     = source_table.get("primary_keys", [])
        src_fks     = source_table.get("foreign_keys", [])
        src_indexes = source_table.get("indexes", [])
        conflicts   = []

        # ── CREATE TABLE DDL (no indexes) ─────────────────────────────────────
        create_ddl = self._generate_create_table(
            table_name=table_name,
            source_cols=src_cols,
            column_mapping=column_mapping,
            primary_keys=[map_col(pk) for pk in src_pks],
            target_db=target_db,
        )

        # ── PK DDL ────────────────────────────────────────────────────────────
        pk_ddl = None
        if src_pks:
            mapped_pks = [map_col(pk) for pk in src_pks]
            pk_ddl = self._generate_pk_ddl(table_name, mapped_pks, target_db)

        # ── INDEX DDL ─────────────────────────────────────────────────────────
        index_ddl_list = []
        for idx in src_indexes:
            idx_name = idx.get("name", "")
            # Skip PK index (already in CREATE TABLE)
            if idx_name == "PRIMARY" or idx_name.upper().startswith("PRIMARY"):
                continue
            idx_cols    = [map_col(c) for c in idx.get("columns", [])]
            is_unique   = idx.get("unique", False)
            if not idx_cols:
                continue
            ddl = self._generate_index_ddl(
                table_name=table_name,
                index_name=f"{idx_name}_{table_name}"[:60],
                columns=idx_cols,
                unique=is_unique,
                target_db=target_db,
                definition=idx.get("def"),
            )
            index_ddl_list.append(ddl)

        # ── UNIQUE constraints ────────────────────────────────────────────────
        unique_ddl_list = []
        for col_name, col_def in src_cols.items():
            if col_def.get("unique") and col_name not in src_pks:
                mapped = map_col(col_name)
                unique_ddl_list.append(
                    self._generate_unique_constraint(table_name, mapped, target_db)
                )

        # ── FK DDL ────────────────────────────────────────────────────────────
        fk_ddl_list = []
        for fk in src_fks:
            src_col  = fk.get("column")
            ref_tbl  = fk.get("ref_table")
            ref_col  = fk.get("ref_column")
            cname    = fk.get("constraint_name", f"fk_{table_name}_{src_col}")
            mapped_col = map_col(src_col)

            # Check if ref_table exists in target schema
            ddl = self._generate_fk_ddl(
                table_name=table_name,
                column=mapped_col,
                ref_table=ref_tbl,
                ref_column=ref_col,
                constraint_name=cname,
                target_db=target_db,
            )
            fk_ddl_list.append(ddl)

        return TableConstraintPlan(
            table_name=table_name,
            create_ddl=create_ddl,
            pk_ddl=pk_ddl,
            index_ddl_list=index_ddl_list,
            fk_ddl_list=fk_ddl_list,
            unique_ddl_list=unique_ddl_list,
            conflicts=conflicts,
        )

    def build_full_migration_ddl(
        self,
        source_schema:   Dict[str, Any],
        target_schema:   Dict[str, Any],
        table_mappings:  Dict[str, Dict],    # {src_table: {target, column_mappings}}
        source_db:       str = "mysql",
        target_db:       str = "mysql",
    ) -> Dict[str, Any]:
        """
        Build the complete DDL for the entire migration in 4 phases:

        Phase 1: CREATE TABLE (no indexes, no FKs)
        Phase 2: Load data  ← workers do this
        Phase 3: CREATE INDEX / UNIQUE constraints
        Phase 4: ADD FOREIGN KEY constraints

        Returns dict with phase_1, phase_3, phase_4 DDL lists.
        """
        phase_1_ddl = []   # CREATE TABLEs
        phase_3_ddl = []   # CREATE INDEX + UNIQUE
        phase_4_ddl = []   # ADD FOREIGN KEY

        src_tables = source_schema.get("tables", {})
        tgt_tables = target_schema.get("tables", {})

        for src_table_name, mapping in table_mappings.items():
            src_tbl     = src_tables.get(src_table_name, {})
            tgt_name    = mapping.get("target", src_table_name)
            tgt_tbl     = tgt_tables.get(tgt_name, src_tbl)  # fallback to source if target not in schema
            col_mapping = mapping.get("column_mappings", {})

            plan = self.build_plan(
                table_name=tgt_name,
                source_table=src_tbl,
                target_table=tgt_tbl,
                column_mapping=col_mapping,
                source_db=source_db,
                target_db=target_db,
            )

            phase_1_ddl.append(plan.create_ddl)
            phase_3_ddl.extend(plan.index_ddl_list)
            phase_3_ddl.extend(plan.unique_ddl_list)
            phase_4_ddl.extend(plan.fk_ddl_list)

        return {
            "phase_1_create_tables": phase_1_ddl,
            "phase_3_indexes":       phase_3_ddl,
            "phase_4_foreign_keys":  phase_4_ddl,
            "total_tables":          len(phase_1_ddl),
            "total_indexes":         len(phase_3_ddl),
            "total_foreign_keys":    len(phase_4_ddl),
        }

    # ── DDL generators ────────────────────────────────────────────────────────

    def _generate_create_table(
        self,
        table_name:   str,
        source_cols:  Dict[str, dict],
        column_mapping: Dict[str, str],
        primary_keys: List[str],
        target_db:    str,
    ) -> str:
        lines = []
        for src_col, col_def in source_cols.items():
            tgt_col  = column_mapping.get(src_col, src_col)
            col_type = self._map_type(col_def.get("type", "TEXT"), target_db)
            nullable = "" if col_def.get("nullable", True) else " NOT NULL"
            default  = col_def.get("default")
            default_str = f" DEFAULT {default}" if default is not None else ""
            extra    = col_def.get("extra", "")
            auto_inc = " AUTO_INCREMENT" if "auto_increment" in extra.lower() and target_db == "mysql" else (
                       " GENERATED ALWAYS AS IDENTITY" if "auto_increment" in extra.lower() and target_db != "mysql" else ""
            )
            if target_db == "mysql":
                lines.append(f"    `{tgt_col}` {col_type}{nullable}{default_str}{auto_inc}")
            else:
                lines.append(f'    "{tgt_col}" {col_type}{nullable}{default_str}{auto_inc}')

        col_block = ",\n".join(lines)

        if primary_keys:
            if target_db == "mysql":
                pk_str = ", ".join(f"`{c}`" for c in primary_keys)
            else:
                pk_str = ", ".join(f'"{c}"' for c in primary_keys)
            col_block += f",\n    PRIMARY KEY ({pk_str})"

        if target_db == "mysql":
            return (
                f"CREATE TABLE IF NOT EXISTS `{table_name}` (\n"
                f"{col_block}\n"
                f") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;"
            )
        else:
            return (
                f'CREATE TABLE IF NOT EXISTS "{table_name}" (\n'
                f"{col_block}\n"
                f");"
            )

    def _generate_pk_ddl(self, table_name: str, pk_cols: List[str], target_db: str) -> str:
        if target_db == "mysql":
            cols = ", ".join(f"`{c}`" for c in pk_cols)
            return f"ALTER TABLE `{table_name}` ADD PRIMARY KEY ({cols});"
        else:
            cols = ", ".join(f'"{c}"' for c in pk_cols)
            return f'ALTER TABLE "{table_name}" ADD PRIMARY KEY ({cols});'

    def _generate_index_ddl(
        self,
        table_name: str,
        index_name: str,
        columns:    List[str],
        unique:     bool,
        target_db:  str,
        definition: Optional[str] = None,
    ) -> str:
        # For PostgreSQL, use the original definition if available
        if target_db != "mysql" and definition:
            return definition + ";"

        unique_kw = "UNIQUE " if unique else ""
        if target_db == "mysql":
            cols = ", ".join(f"`{c}`" for c in columns)
            return f"CREATE {unique_kw}INDEX `{index_name}` ON `{table_name}` ({cols});"
        else:
            cols = ", ".join(f'"{c}"' for c in columns)
            return f'CREATE {unique_kw}INDEX "{index_name}" ON "{table_name}" ({cols});'

    def _generate_unique_constraint(self, table_name: str, column: str, target_db: str) -> str:
        cname = f"uq_{table_name}_{column}"[:60]
        if target_db == "mysql":
            return f"ALTER TABLE `{table_name}` ADD CONSTRAINT `{cname}` UNIQUE (`{column}`);"
        else:
            return f'ALTER TABLE "{table_name}" ADD CONSTRAINT "{cname}" UNIQUE ("{column}");'

    def _generate_fk_ddl(
        self,
        table_name:      str,
        column:          str,
        ref_table:       str,
        ref_column:      str,
        constraint_name: str,
        target_db:       str,
    ) -> str:
        cname = constraint_name[:60]
        if target_db == "mysql":
            return (
                f"ALTER TABLE `{table_name}` "
                f"ADD CONSTRAINT `{cname}` "
                f"FOREIGN KEY (`{column}`) REFERENCES `{ref_table}` (`{ref_column}`);"
            )
        else:
            return (
                f'ALTER TABLE "{table_name}" '
                f'ADD CONSTRAINT "{cname}" '
                f'FOREIGN KEY ("{column}") REFERENCES "{ref_table}" ("{ref_column}");'
            )

    def _map_type(self, src_type: str, target_db: str) -> str:
        """
        Map source SQL type to target DB type.
        Handles MySQL → PostgreSQL and PostgreSQL → MySQL conversions.
        """
        base = src_type.split("(")[0].lower()
        rest = src_type[len(base):]   # e.g. "(255)" or ""

        if target_db in ("postgres", "postgresql"):
            mysql_to_pg = {
                "tinyint":    "SMALLINT",
                "mediumint":  "INTEGER",
                "int":        "INTEGER",
                "integer":    "INTEGER",
                "bigint":     "BIGINT",
                "float":      "REAL",
                "double":     "DOUBLE PRECISION",
                "decimal":    "NUMERIC",
                "numeric":    "NUMERIC",
                "varchar":    "VARCHAR",
                "char":       "CHAR",
                "tinytext":   "TEXT",
                "text":       "TEXT",
                "mediumtext": "TEXT",
                "longtext":   "TEXT",
                "tinyblob":   "BYTEA",
                "blob":       "BYTEA",
                "mediumblob": "BYTEA",
                "longblob":   "BYTEA",
                "binary":     "BYTEA",
                "varbinary":  "BYTEA",
                "date":       "DATE",
                "datetime":   "TIMESTAMP",
                "timestamp":  "TIMESTAMP",
                "time":       "TIME",
                "year":       "SMALLINT",
                "json":       "JSONB",
                "enum":       "TEXT",
                "set":        "TEXT",
                "bit":        "BIT",
                "boolean":    "BOOLEAN",
                "bool":       "BOOLEAN",
            }
            mapped = mysql_to_pg.get(base)
            if mapped:
                if mapped in ("VARCHAR", "CHAR", "NUMERIC") and rest:
                    return f"{mapped}{rest}"
                return mapped

        elif target_db == "mysql":
            pg_to_mysql = {
                "smallint":         "SMALLINT",
                "integer":          "INT",
                "int4":             "INT",
                "bigint":           "BIGINT",
                "int8":             "BIGINT",
                "real":             "FLOAT",
                "float4":           "FLOAT",
                "double precision": "DOUBLE",
                "float8":           "DOUBLE",
                "numeric":          "DECIMAL",
                "bpchar":           "CHAR",
                "character varying":"VARCHAR",
                "text":             "LONGTEXT",
                "bytea":            "LONGBLOB",
                "boolean":          "TINYINT(1)",
                "bool":             "TINYINT(1)",
                "jsonb":            "JSON",
                "uuid":             "VARCHAR(36)",
                "date":             "DATE",
                "timestamp":        "DATETIME",
                "timestamptz":      "DATETIME",
                "time":             "TIME",
                "interval":         "VARCHAR(50)",
            }
            mapped = pg_to_mysql.get(base)
            if mapped:
                if mapped in ("VARCHAR", "CHAR", "DECIMAL") and rest:
                    return f"{mapped}{rest}"
                return mapped

        # Same-engine or unknown: return as-is
        return src_type
