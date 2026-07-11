"""
Object Storage Connector
File: migration/backend/connectors/object_storage/object_storage_connector.py

Implements DatabaseConnector for cloud object storage:
    AWS S3, Azure Blob Storage, Google Cloud Storage

Design: an S3 bucket is treated as a "database", an object key prefix
as a "table". Files inside the prefix are discovered and streamed through
the FileConnector internally — no duplicated parsing logic.

config:
    provider:    "s3" | "azure" | "gcs"
    bucket:      bucket/container name (used as "database")
    prefix:      optional key prefix filter
    format:      "csv" | "parquet" | "json" | "excel" (default: "csv")
    region:      AWS region (S3 only)
    access_key:  AWS/GCS key (or use IAM role/env vars)
    secret_key:  AWS/GCS secret
    connection_string: Azure blob connection string
    account_name: Azure account name
    account_key:  Azure account key

Requirements:
    pip install boto3 azure-storage-blob google-cloud-storage

Capabilities:
    discover=True   → list objects, infer schema from sample
    stream_read=True → download + stream through FileConnector
    bulk_write=True  → write to temp file, upload to bucket
    cdc=False        → object storage has no change stream (use S3 event notifications externally)
    checksum=True    → ETag-based checksum
"""

import os
import time
import tempfile
from typing import Dict, Any, List, Generator, Optional

from backend.connector_framework.base.base_connector import (
    DatabaseConnector, ConnectorCapabilities, SchemaInfo, BulkWriteResult
)
from backend.shared.config.logging import logger


class ObjectStorageConnector(DatabaseConnector):

    @property
    def name(self) -> str:
        return "object_storage"

    @property
    def display_name(self) -> str:
        provider = self.config.get("provider", "s3").upper()
        return f"Object Storage ({provider})"

    @property
    def capabilities(self) -> ConnectorCapabilities:
        return ConnectorCapabilities(
            discover=True, stream_read=True, bulk_write=True,
            cdc=False, checksum=True, constraints=False,
            indexes=False, jsonb=False, partitioning=False,
        )

    def connect(self) -> None:
        provider = self.config.get("provider", "s3").lower()
        if provider == "s3":
            self._client = self._make_s3_client()
        elif provider == "azure":
            self._client = self._make_azure_client()
        elif provider in ("gcs", "gcp"):
            self._client = self._make_gcs_client()
        else:
            raise ValueError(f"Unsupported provider: {provider}")
        logger.info("ObjectStorageConnector connected",
                    provider=provider, bucket=self.config.get("bucket"))

    def disconnect(self) -> None:
        self._client = None

    def test_connection(self) -> Dict[str, Any]:
        start = time.time()
        try:
            self.connect()
            provider = self.config.get("provider", "s3").lower()
            bucket   = self.config.get("bucket", "")

            if provider == "s3":
                self._client.head_bucket(Bucket=bucket)
                version = "AWS S3"
            elif provider == "azure":
                container = self._client.get_container_client(bucket)
                container.get_container_properties()
                version = "Azure Blob Storage"
            elif provider in ("gcs", "gcp"):
                self._client.bucket(bucket)
                version = "Google Cloud Storage"

            return {
                "success": True, "db_version": version,
                "latency_ms": int((time.time() - start) * 1000), "error": None,
            }
        except Exception as e:
            return {
                "success": False, "db_version": None,
                "latency_ms": int((time.time() - start) * 1000), "error": str(e),
            }

    def discover_schema(self) -> SchemaInfo:
        """List objects in bucket/prefix and infer schema by downloading a sample."""
        provider = self.config.get("provider", "s3").lower()
        bucket   = self.config.get("bucket", "")
        prefix   = self.config.get("prefix", "")
        fmt      = self.config.get("format", "csv")
        tables   = {}

        object_keys = self._list_objects(provider, bucket, prefix)

        for key in object_keys[:50]:   # Limit discovery to 50 objects
            # Derive table name from object key (filename without extension)
            table_name = os.path.splitext(os.path.basename(key))[0]
            if not table_name:
                continue

            try:
                # Download sample to temp file, use FileConnector to read schema
                with tempfile.NamedTemporaryFile(
                    suffix=f".{fmt}", delete=False
                ) as tmp:
                    tmp_path = tmp.name
                    self._download_object(provider, bucket, key, tmp_path, max_bytes=65536)

                from backend.connectors.file.file_connector import FileConnector
                fc = FileConnector({"database": os.path.dirname(tmp_path), "format": fmt})
                schema = fc.discover_schema()

                for tname, tdef in schema.tables.items():
                    tables[table_name] = {**tdef, "object_key": key, "bucket": bucket}
                    break

                os.unlink(tmp_path)
            except Exception as e:
                logger.debug("Object schema discovery failed", key=key, error=str(e))

        return SchemaInfo(
            database=f"{self.config.get('provider','s3')}://{bucket}",
            engine="object_storage",
            tables=tables,
        )

    def get_row_count(self, table_name: str) -> int:
        # Estimate based on object size
        try:
            provider = self.config.get("provider", "s3").lower()
            bucket   = self.config.get("bucket", "")
            key      = self._find_object_key(provider, bucket, table_name)
            size     = self._get_object_size(provider, bucket, key)
            return max(size // 256, 1)   # rough estimate
        except Exception:
            return 0

    def get_avg_row_size(self, table_name: str) -> int:
        return 512

    def stream_rows(
        self, table_name, pk_column, pk_start, pk_end,
        columns=None, batch_size=5000
    ) -> Generator[Dict[str, Any], None, None]:
        """Download object to temp file, stream through FileConnector."""
        provider = self.config.get("provider", "s3").lower()
        bucket   = self.config.get("bucket", "")
        fmt      = self.config.get("format", "csv")
        key      = self._find_object_key(provider, bucket, table_name)

        with tempfile.NamedTemporaryFile(suffix=f".{fmt}", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            self._download_object(provider, bucket, key, tmp_path)
            fc = FileConnector({
                "database": os.path.dirname(tmp_path),
                "format":   fmt,
            })
            # Use the temp filename as table name
            tmp_table = os.path.splitext(os.path.basename(tmp_path))[0]
            for row in fc.stream_rows(tmp_table, pk_column, pk_start, pk_end,
                                      columns=columns, batch_size=batch_size):
                yield row
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

    def bulk_insert(self, table_name, rows, mode="ignore_duplicates") -> BulkWriteResult:
        """Write rows to a temp file, then upload to object storage."""
        if not rows:
            return BulkWriteResult(0, 0, 0, 0)

        provider = self.config.get("provider", "s3").lower()
        bucket   = self.config.get("bucket", "")
        fmt      = self.config.get("format", "csv")
        prefix   = self.config.get("prefix", "")
        start    = time.time()

        ext_map  = {"csv": ".csv", "json": ".json", "parquet": ".parquet", "excel": ".xlsx"}
        ext      = ext_map.get(fmt, ".csv")
        key      = f"{prefix}{table_name}{ext}".lstrip("/")

        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
            tmp_path = tmp.name

        try:
            fc = FileConnector({
                "database": os.path.dirname(tmp_path),
                "format":   fmt,
            })
            tmp_table = os.path.splitext(os.path.basename(tmp_path))[0]
            result    = fc.bulk_insert(tmp_table, rows, mode)

            self._upload_object(provider, bucket, key, tmp_path)
            elapsed = int((time.time() - start) * 1000)
            return BulkWriteResult(result.rows_inserted, 0, result.rows_failed, elapsed)
        except Exception as e:
            return BulkWriteResult(0, 0, len(rows), int((time.time()-start)*1000), str(e))
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

    def count_rows_in_range(self, table_name, pk_column, pk_start, pk_end) -> int:
        return sum(1 for _ in self.stream_rows(table_name, pk_column, pk_start, pk_end))

    def compute_checksum(self, table_name, pk_column, pk_start, pk_end) -> str:
        """Use object ETag as checksum (set by S3/Azure/GCS on upload)."""
        try:
            provider = self.config.get("provider", "s3").lower()
            bucket   = self.config.get("bucket", "")
            key      = self._find_object_key(provider, bucket, table_name)
            return self._get_etag(provider, bucket, key)
        except Exception:
            return "unknown"

    # ── Provider-specific helpers ──────────────────────────────────────────────

    def _make_s3_client(self):
        import boto3
        kwargs = {}
        if self.config.get("access_key"):
            kwargs["aws_access_key_id"]     = self.config["access_key"]
            kwargs["aws_secret_access_key"]  = self.config["secret_key"]
        if self.config.get("region"):
            kwargs["region_name"]            = self.config["region"]
        if self.config.get("endpoint_url"):
            kwargs["endpoint_url"]           = self.config["endpoint_url"]
        return boto3.client("s3", **kwargs)

    def _make_azure_client(self):
        from azure.storage.blob import BlobServiceClient
        cs = self.config.get("connection_string")
        if cs:
            return BlobServiceClient.from_connection_string(cs)
        return BlobServiceClient(
            account_url=f"https://{self.config['account_name']}.blob.core.windows.net",
            credential=self.config.get("account_key"),
        )

    def _make_gcs_client(self):
        from google.cloud import storage as gcs
        return gcs.Client()

    def _list_objects(self, provider, bucket, prefix) -> List[str]:
        try:
            if provider == "s3":
                resp = self._client.list_objects_v2(Bucket=bucket, Prefix=prefix, MaxKeys=100)
                return [o["Key"] for o in resp.get("Contents", [])]
            elif provider == "azure":
                cc = self._client.get_container_client(bucket)
                return [b.name for b in cc.list_blobs(name_starts_with=prefix)]
            elif provider in ("gcs", "gcp"):
                bkt = self._client.bucket(bucket)
                return [b.name for b in self._client.list_blobs(bkt, prefix=prefix)]
        except Exception as e:
            logger.warning("Object listing failed", error=str(e))
            return []

    def _find_object_key(self, provider, bucket, table_name) -> str:
        fmt    = self.config.get("format", "csv")
        prefix = self.config.get("prefix", "")
        ext_map = {"csv": ".csv", "json": ".json", "parquet": ".parquet", "excel": ".xlsx"}
        ext    = ext_map.get(fmt, ".csv")
        return f"{prefix}{table_name}{ext}".lstrip("/")

    def _get_object_size(self, provider, bucket, key) -> int:
        try:
            if provider == "s3":
                return self._client.head_object(Bucket=bucket, Key=key)["ContentLength"]
            elif provider == "azure":
                props = self._client.get_blob_client(bucket, key).get_blob_properties()
                return props.size
            elif provider in ("gcs", "gcp"):
                return self._client.bucket(bucket).blob(key).size or 0
        except Exception:
            return 0

    def _get_etag(self, provider, bucket, key) -> str:
        try:
            if provider == "s3":
                return self._client.head_object(Bucket=bucket, Key=key).get("ETag", "")[:16]
            elif provider == "azure":
                props = self._client.get_blob_client(bucket, key).get_blob_properties()
                return str(props.etag or "")[:16]
        except Exception:
            return "unknown"

    def _download_object(self, provider, bucket, key, local_path, max_bytes=None):
        if provider == "s3":
            if max_bytes:
                resp = self._client.get_object(Bucket=bucket, Key=key,
                                                Range=f"bytes=0-{max_bytes}")
                with open(local_path, "wb") as f:
                    f.write(resp["Body"].read())
            else:
                self._client.download_file(bucket, key, local_path)
        elif provider == "azure":
            bc   = self._client.get_blob_client(bucket, key)
            data = bc.download_blob(offset=0, length=max_bytes).readall()
            with open(local_path, "wb") as f:
                f.write(data)
        elif provider in ("gcs", "gcp"):
            blob = self._client.bucket(bucket).blob(key)
            blob.download_to_filename(local_path)

    def _upload_object(self, provider, bucket, key, local_path):
        if provider == "s3":
            self._client.upload_file(local_path, bucket, key)
        elif provider == "azure":
            with open(local_path, "rb") as f:
                self._client.get_blob_client(bucket, key).upload_blob(f, overwrite=True)
        elif provider in ("gcs", "gcp"):
            self._client.bucket(bucket).blob(key).upload_from_filename(local_path)


# Alias for backward compatibility with ConnectorRegistry
from backend.connector_framework.base.base_connector import DatabaseConnector

from backend.connectors.file.file_connector import FileConnector
