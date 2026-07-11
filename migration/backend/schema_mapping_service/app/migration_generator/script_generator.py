"""
Script Generator
File: migration/backend/schema_mapping_service/app/migration_generator/script_generator.py

Generates Python and SQL migration script templates for tables that have
unsafe conversions or complex transformations that need human review.

Much better than the old tool's single generate_migration_script():
  - Generates both Python and SQL formats
  - Handles single / split / merge mappings
  - Includes full column mapping and CAST placeholders
  - Saved to DB as GeneratedScript records (downloadable via API)
  - Produces Airflow DAG skeleton when requested
"""

import datetime
from typing import Dict, Any, List


def generate_python_script(
    source_table:    str,
    target_table:    str,
    column_mappings: Dict[str, str],   # {src_col: tgt_col}
    source_config:   Dict[str, Any],
    target_config:   Dict[str, Any],
    mapping_type:    str = "single",   # single | split | merge
    join_condition:  str = "",
    unsafe_cols:     List[Dict] = None,
) -> str:
    """Generate a Python migration script template."""
    unsafe_cols = unsafe_cols or []
    now         = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    src_engine  = source_config.get("engine", "mysql")
    tgt_engine  = target_config.get("engine", "mysql")

    col_lines = ""
    for src_col, tgt_col in column_mappings.items():
        is_unsafe = any(u["source_column"] == src_col for u in unsafe_cols)
        comment = "  # ⚠ UNSAFE CONVERSION — REVIEW REQUIRED" if is_unsafe else ""
        col_lines += f'        "{src_col}": row["{src_col}"],{comment}\n'

    script = f'''#!/usr/bin/env python3
"""
Manual Migration Script
Generated: {now}
Source:    {src_engine} → {source_table}
Target:    {tgt_engine} → {target_table}
Mapping:   {mapping_type}

⚠ This script was generated because one or more columns require
  manual type conversion or transformation. Review all marked lines
  before running this script in production.
"""

import mysql.connector   # or psycopg2 for PostgreSQL
import datetime
import decimal

# ── Connection config ─────────────────────────────────────────────────────────
SOURCE_CONFIG = {{
    "host":     "{source_config.get('host', 'localhost')}",
    "port":     {source_config.get('port', 3306)},
    "database": "{source_config.get('database', 'source_db')}",
    "user":     "{source_config.get('user', 'root')}",
    "password": "YOUR_SOURCE_PASSWORD",
}}

TARGET_CONFIG = {{
    "host":     "{target_config.get('host', 'localhost')}",
    "port":     {target_config.get('port', 3306)},
    "database": "{target_config.get('database', 'target_db')}",
    "user":     "{target_config.get('user', 'root')}",
    "password": "YOUR_TARGET_PASSWORD",
}}

CHUNK_SIZE  = 10_000   # rows per batch
SOURCE_TABLE = "{source_table}"
TARGET_TABLE = "{target_table}"


def transform_row(row: dict) -> dict:
    """
    Transform a single source row into a target row.
    ⚠ Review and modify conversion logic for all marked columns.
    """
    return {{
{col_lines}    }}


def migrate():
    src_conn = mysql.connector.connect(**SOURCE_CONFIG)
    tgt_conn = mysql.connector.connect(**TARGET_CONFIG)
    src_cur  = src_conn.cursor(dictionary=True, buffered=False)
    tgt_cur  = tgt_conn.cursor()

    # Count total rows for progress tracking
    src_cur.execute(f"SELECT COUNT(*) AS cnt FROM `{{SOURCE_TABLE}}`")
    total = src_cur.fetchone()["cnt"]
    print(f"Migrating {{total:,}} rows from {{SOURCE_TABLE}} → {{TARGET_TABLE}}")

    # Stream source data in chunks
    src_cur.execute(f"SELECT * FROM `{{SOURCE_TABLE}}` ORDER BY id")

    batch     = []
    processed = 0

    while True:
        rows = src_cur.fetchmany(CHUNK_SIZE)
        if not rows:
            break

        for row in rows:
            target_row = transform_row(row)
            batch.append(tuple(target_row.values()))

        if batch:
            columns      = list(transform_row(rows[0]).keys())
            col_names    = ", ".join(f"`{{c}}`" for c in columns)
            placeholders = ", ".join(["%s"] * len(columns))

            # INSERT IGNORE = safe to re-run (idempotent)
            tgt_cur.executemany(
                f"INSERT IGNORE INTO `{{TARGET_TABLE}}` ({{col_names}}) VALUES ({{placeholders}})",
                batch
            )
            tgt_conn.commit()
            processed += len(batch)
            batch = []
            print(f"  Progress: {{processed:,}} / {{total:,}} ({{{processed/total*100:.1f}}}%)")

    # Validate
    tgt_cur.execute(f"SELECT COUNT(*) FROM `{{TARGET_TABLE}}`")
    target_count = tgt_cur.fetchone()[0]
    print(f"\\nValidation: source={{total:,}}, target={{target_count:,}}")
    if total == target_count:
        print("✅ Migration successful — row counts match")
    else:
        print("❌ WARNING: Row count mismatch — please investigate")

    src_cur.close()
    tgt_cur.close()
    src_conn.close()
    tgt_conn.close()


if __name__ == "__main__":
    migrate()
'''
    return script


def generate_sql_script(
    source_table:    str,
    target_table:    str,
    column_mappings: Dict[str, str],
    source_db:       str = "source_db",
    target_db:       str = "target_db",
    mapping_type:    str = "single",
    join_condition:  str = "",
    unsafe_cols:     List[Dict] = None,
) -> str:
    """Generate a SQL migration script template."""
    unsafe_cols = unsafe_cols or []
    now         = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    col_select_parts = []
    col_insert_parts = []

    for src_col, tgt_col in column_mappings.items():
        is_unsafe = any(u["source_column"] == src_col for u in unsafe_cols)
        if is_unsafe:
            # Show placeholder for manual conversion
            from_type = next((u["from_type"] for u in unsafe_cols if u["source_column"] == src_col), "?")
            to_type   = next((u["to_type"]   for u in unsafe_cols if u["source_column"] == src_col), "?")
            col_select_parts.append(
                f"    /* ⚠ UNSAFE: {src_col} ({from_type} → {to_type}) — add conversion here */\n"
                f"    `{src_col}` /* TODO: CAST or transform */ AS `{tgt_col}`"
            )
        else:
            if src_col == tgt_col:
                col_select_parts.append(f"    `{src_col}`")
            else:
                col_select_parts.append(f"    `{src_col}` AS `{tgt_col}`")
        col_insert_parts.append(f"`{tgt_col}`")

    select_expr  = ",\n".join(col_select_parts)
    insert_cols  = ", ".join(col_insert_parts)
    from_clause  = f"`{source_db}`.`{source_table}`"

    if mapping_type == "merge" and join_condition:
        # Merge: SELECT from multiple joined source tables
        from_clause = f"/* TODO: add JOIN tables here */\n    {from_clause}\n    {join_condition}"
    elif mapping_type == "split":
        from_clause = f"{from_clause}\n    /* TODO: add WHERE condition to split rows */"

    script = f"""-- ============================================================
-- Manual Migration SQL Script
-- Generated: {now}
-- Source:    {source_db}.{source_table}  ({mapping_type} mapping)
-- Target:    {target_db}.{target_table}
--
-- ⚠ Review all TODO and UNSAFE markers before running.
-- ============================================================

-- Step 1: Disable FK checks and indexes for faster load
SET FOREIGN_KEY_CHECKS = 0;
ALTER TABLE `{target_db}`.`{target_table}` DISABLE KEYS;

-- Step 2: Insert data
INSERT INTO `{target_db}`.`{target_table}` ({insert_cols})
SELECT
{select_expr}
FROM {from_clause}
;

-- Step 3: Re-enable FK checks and rebuild indexes
ALTER TABLE `{target_db}`.`{target_table}` ENABLE KEYS;
SET FOREIGN_KEY_CHECKS = 1;

-- Step 4: Validate row counts
SELECT
    (SELECT COUNT(*) FROM `{source_db}`.`{source_table}`) AS source_count,
    (SELECT COUNT(*) FROM `{target_db}`.`{target_table}`) AS target_count;
"""
    return script


def generate_airflow_dag(
    project_name: str,
    tables:       List[str],
    source_config: Dict[str, Any],
    target_config: Dict[str, Any],
) -> str:
    """Generate an Apache Airflow DAG skeleton for the migration."""
    now      = datetime.datetime.utcnow().strftime("%Y-%m-%d")
    dag_id   = project_name.lower().replace(" ", "_").replace("-", "_")

    task_defs = ""
    task_ids  = []
    for table in tables:
        task_id = f"migrate_{table}"
        task_ids.append(task_id)
        task_defs += f"""
    {task_id} = PythonOperator(
        task_id="{task_id}",
        python_callable=migrate_table,
        op_kwargs={{"table_name": "{table}"}},
        dag=dag,
    )
"""

    deps = ""
    for i in range(len(task_ids) - 1):
        deps += f"    {task_ids[i]} >> {task_ids[i+1]}\n"

    dag = f"""\"\"\"
Airflow DAG — {project_name}
Generated: {now}

Run with:
    airflow dags trigger {dag_id}
\"\"\"

from datetime import datetime, timedelta
from airflow import DAG
from airflow.operators.python import PythonOperator


SOURCE_CONFIG = {source_config}
TARGET_CONFIG = {target_config}


def migrate_table(table_name: str, **kwargs):
    \"\"\"Migrate a single table. Replace with actual migration call.\"\"\"
    print(f"Migrating table: {{table_name}}")
    # TODO: call your chunk executor here
    # executor.migrate_table(SOURCE_CONFIG, TARGET_CONFIG, table_name)


default_args = {{
    "owner":            "migration_platform",
    "depends_on_past":  False,
    "retries":          3,
    "retry_delay":      timedelta(minutes=5),
    "start_date":       datetime({now.replace('-', ', ')}),
}}

with DAG(
    dag_id="{dag_id}",
    default_args=default_args,
    schedule_interval=None,
    catchup=False,
    tags=["migration"],
) as dag:
{task_defs}
    # Task dependencies
{deps if deps else "    pass  # Single table — no dependencies"}
"""
    return dag
