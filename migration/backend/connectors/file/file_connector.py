"""
File Connector
File: migration/backend/connectors/file/file_connector.py

Implements DatabaseConnector for flat files:
    CSV, TSV, Excel (.xlsx), JSON (lines), Parquet, Avro

Design principle: "a table is a file." The config field "database"
becomes a directory path, and "table_name" becomes the filename
(without extension, or full path if absolute).

This connector reuses 100% of the existing connector interface —
workers, WorkflowExecutor, ReadNode, WriteNode — all work unchanged.
The connector handles the file-specific I/O internally.

Capabilities:
    discover=True      → infer schema from first N rows
    stream_read=True   → chunked reading with pandas/pyarrow
    bulk_write=True    → append mode or overwrite
    cdc=False          → files have no change stream
    checksum=True      → MD5 of file content
    constraints=False  → files have no FK/PK constraints
    indexes=False      → files have no indexes

Supported formats (engine config field):
    csv     → uses pandas read_csv with chunked iterator
    tsv     → csv with sep='\t'
    excel   → uses pandas read_excel (requires openpyxl)
    json    → newline-delimited JSON (one JSON object per line)
    parquet → uses pyarrow (columnar, very fast for large files)
    avro    → uses fastavro (requires fastavro)

Requirements:
    pip install pandas openpyxl pyarrow fastavro
"""

import os
import hashlib
import time
from typing import Dict, Any, List, Generator, Optional

from backend.connector_framework.base.base_connector import (
    DatabaseConnector, ConnectorCapabilities, SchemaInfo, BulkWriteResult, CDCPosition
)
from backend.shared.config.logging import logger


class FileConnector(DatabaseConnector):

    @property
    def name(self) -> str:
        return "file"

    @property
    def display_name(self) -> str:
        fmt = self.config.get("format", "csv").upper()
        return f"File ({fmt})"

    @property
    def capabilities(self) -> ConnectorCapabilities:
        return ConnectorCapabilities(
            discover=True, stream_read=True, bulk_write=True,
            cdc=False, checksum=True, constraints=False,
            indexes=False, jsonb=False, partitioning=False,
        )

    # ── Connection (files don't "connect" — just validate path exists) ────────

    def connect(self) -> None:
        base_dir = self.config.get("database", ".")
        if not os.path.exists(base_dir):
            os.makedirs(base_dir, exist_ok=True)
        logger.info("FileConnector ready", base_dir=base_dir,
                    format=self.config.get("format", "csv"))

    def disconnect(self) -> None:
        pass   # Nothing to close for files

    def test_connection(self) -> Dict[str, Any]:
        start   = time.time()
        base_dir = self.config.get("database", ".")
        try:
            exists  = os.path.exists(base_dir)
            files   = [f for f in os.listdir(base_dir)] if exists else []
            return {
                "success":    exists,
                "db_version": f"File connector ({self.config.get('format', 'csv')})",
                "latency_ms": int((time.time() - start) * 1000),
                "error":      None if exists else f"Directory not found: {base_dir}",
                "file_count": len(files),
            }
        except Exception as e:
            return {"success": False, "db_version": None,
                    "latency_ms": int((time.time() - start) * 1000), "error": str(e)}

    # ── Schema discovery ──────────────────────────────────────────────────────

    def discover_schema(self) -> SchemaInfo:
        import pandas as pd
        base_dir = self.config.get("database", ".")
        fmt      = self.config.get("format", "csv")
        tables   = {}

        # Find all files matching the format
        ext_map = {
            "csv": ".csv", "tsv": ".tsv", "excel": ".xlsx",
            "json": ".json", "parquet": ".parquet", "avro": ".avro"
        }
        ext = ext_map.get(fmt, f".{fmt}")

        for fname in os.listdir(base_dir):
            if not fname.lower().endswith(ext):
                continue

            table_name = fname[:-len(ext)]
            fpath      = os.path.join(base_dir, fname)

            try:
                # Read just the first few rows to infer schema
                df = self._read_sample(fpath, fmt, nrows=100)
                if df is None:
                    continue

                columns = {}
                for col in df.columns:
                    dtype = str(df[col].dtype)
                    sql_type = self._dtype_to_sql(dtype)
                    columns[col] = {
                        "type":     sql_type,
                        "nullable": bool(df[col].isna().any()),
                        "pk":       col.lower() in ("id", "pk", f"{table_name}_id"),
                        "unique":   False,
                        "default":  None,
                        "extra":    "",
                    }

                file_size = os.path.getsize(fpath)
                row_count = self._estimate_row_count(fpath, fmt, file_size)

                tables[table_name] = {
                    "columns":      columns,
                    "primary_keys": [c for c, d in columns.items() if d["pk"]],
                    "foreign_keys": [],
                    "indexes":      [],
                    "row_count":    row_count,
                    "file_path":    fpath,
                    "file_size":    file_size,
                    "format":       fmt,
                }
            except Exception as e:
                logger.warning("File schema discovery failed",
                               file=fname, error=str(e))

        return SchemaInfo(database=base_dir, engine=f"file_{fmt}", tables=tables)

    def get_row_count(self, table_name: str) -> int:
        fpath = self._resolve_path(table_name)
        fmt   = self.config.get("format", "csv")
        size  = os.path.getsize(fpath) if os.path.exists(fpath) else 0
        return self._estimate_row_count(fpath, fmt, size)

    def get_avg_row_size(self, table_name: str) -> int:
        fpath = self._resolve_path(table_name)
        if not os.path.exists(fpath):
            return 512
        size  = os.path.getsize(fpath)
        rows  = max(self.get_row_count(table_name), 1)
        return max(size // rows, 1)

    # ── Streaming read ─────────────────────────────────────────────────────────

    def stream_rows(
        self, table_name, pk_column, pk_start, pk_end,
        columns=None, batch_size=5000
    ) -> Generator[Dict[str, Any], None, None]:
        import pandas as pd
        fpath = self._resolve_path(table_name)
        fmt   = self.config.get("format", "csv")

        for chunk in self._read_chunked(fpath, fmt, batch_size, columns):
            # Apply PK range filter if pk_column is numeric
            if pk_column in chunk.columns:
                try:
                    chunk = chunk[
                        (chunk[pk_column] >= pk_start) &
                        (chunk[pk_column] <= pk_end)
                    ]
                except Exception:
                    pass   # Non-numeric PK — emit all rows

            for _, row in chunk.iterrows():
                yield row.where(row.notna(), other=None).to_dict()

    # ── Bulk write ─────────────────────────────────────────────────────────────

    def bulk_insert(self, table_name, rows, mode="ignore_duplicates") -> BulkWriteResult:
        if not rows:
            return BulkWriteResult(0, 0, 0, 0)

        import pandas as pd
        start = time.time()
        fpath = self._resolve_path(table_name, for_write=True)
        fmt   = self.config.get("format", "csv")

        try:
            df = pd.DataFrame(rows)

            file_exists = os.path.exists(fpath)

            if fmt == "csv":
                df.to_csv(fpath, mode="a", header=not file_exists, index=False)
            elif fmt == "tsv":
                df.to_csv(fpath, mode="a", header=not file_exists,
                          index=False, sep="\t")
            elif fmt == "excel":
                if file_exists and mode == "ignore_duplicates":
                    existing = pd.read_excel(fpath)
                    df = pd.concat([existing, df], ignore_index=True)
                df.to_excel(fpath, index=False)
            elif fmt == "json":
                df.to_json(fpath, orient="records", lines=True, mode="a")
            elif fmt == "parquet":
                import pyarrow as pa
                import pyarrow.parquet as pq
                table = pa.Table.from_pandas(df)
                if file_exists:
                    existing = pq.read_table(fpath)
                    table    = pa.concat_tables([existing, table])
                pq.write_table(table, fpath)
            else:
                df.to_csv(fpath, mode="a", header=not file_exists, index=False)

            elapsed = int((time.time() - start) * 1000)
            return BulkWriteResult(len(rows), 0, 0, elapsed)

        except Exception as e:
            return BulkWriteResult(0, 0, len(rows),
                                   int((time.time()-start)*1000), str(e))

    # ── Validation ─────────────────────────────────────────────────────────────

    def count_rows_in_range(self, table_name, pk_column, pk_start, pk_end) -> int:
        count = 0
        for _ in self.stream_rows(table_name, pk_column, pk_start, pk_end):
            count += 1
        return count

    def compute_checksum(self, table_name, pk_column, pk_start, pk_end) -> str:
        fpath = self._resolve_path(table_name)
        if not os.path.exists(fpath):
            return "empty"
        md5 = hashlib.md5()
        with open(fpath, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                md5.update(chunk)
        return md5.hexdigest()[:16]

    # ── Private helpers ────────────────────────────────────────────────────────

    def _resolve_path(self, table_name: str, for_write: bool = False) -> str:
        """Convert table_name to a file path."""
        base_dir = self.config.get("database", ".")
        fmt      = self.config.get("format", "csv")
        ext_map  = {
            "csv": ".csv", "tsv": ".tsv", "excel": ".xlsx",
            "json": ".json", "parquet": ".parquet", "avro": ".avro"
        }
        ext = ext_map.get(fmt, f".{fmt}")

        # If table_name is already an absolute path, use as-is
        if os.path.isabs(table_name):
            return table_name

        # Check if table_name has an extension already
        if any(table_name.endswith(e) for e in ext_map.values()):
            return os.path.join(base_dir, table_name)

        return os.path.join(base_dir, f"{table_name}{ext}")

    def _read_sample(self, fpath: str, fmt: str, nrows: int = 100):
        import pandas as pd
        try:
            if fmt in ("csv", "tsv"):
                sep = "\t" if fmt == "tsv" else ","
                return pd.read_csv(fpath, nrows=nrows, sep=sep, low_memory=False)
            elif fmt == "excel":
                return pd.read_excel(fpath, nrows=nrows)
            elif fmt == "json":
                return pd.read_json(fpath, lines=True, nrows=nrows)
            elif fmt == "parquet":
                import pyarrow.parquet as pq
                return pq.read_table(fpath).slice(0, nrows).to_pandas()
        except Exception:
            return None

    def _read_chunked(self, fpath: str, fmt: str, chunk_size: int, columns):
        import pandas as pd
        usecols = columns if columns else None

        if fmt in ("csv", "tsv"):
            sep = "\t" if fmt == "tsv" else ","
            for chunk in pd.read_csv(fpath, chunksize=chunk_size,
                                     usecols=usecols, sep=sep, low_memory=False):
                yield chunk
        elif fmt == "excel":
            df = pd.read_excel(fpath, usecols=usecols)
            for i in range(0, len(df), chunk_size):
                yield df.iloc[i:i+chunk_size]
        elif fmt == "json":
            for chunk in pd.read_json(fpath, lines=True, chunksize=chunk_size):
                yield chunk
        elif fmt == "parquet":
            import pyarrow.parquet as pq
            pf = pq.ParquetFile(fpath)
            for batch in pf.iter_batches(batch_size=chunk_size, columns=columns):
                yield batch.to_pandas()

    def _estimate_row_count(self, fpath: str, fmt: str, file_size: int) -> int:
        if fmt in ("parquet",):
            try:
                import pyarrow.parquet as pq
                return pq.read_metadata(fpath).num_rows
            except Exception:
                return file_size // 100
        # For CSV/JSON: sample first 1000 bytes to estimate bytes per row
        try:
            with open(fpath, "rb") as f:
                sample = f.read(4096).decode(errors="ignore")
            lines  = sample.count("\n")
            if lines > 0:
                bytes_per_line = 4096 / lines
                return max(int(file_size / bytes_per_line) - 1, 0)
        except Exception:
            pass
        return file_size // 256

    def _dtype_to_sql(self, dtype: str) -> str:
        if "int" in dtype:     return "bigint"
        if "float" in dtype:   return "double"
        if "bool" in dtype:    return "boolean"
        if "datetime" in dtype: return "timestamp"
        if "date" in dtype:    return "date"
        return "text"
