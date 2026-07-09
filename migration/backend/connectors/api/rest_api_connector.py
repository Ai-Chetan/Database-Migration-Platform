"""
REST API Connector
File: migration/backend/connectors/api/rest_api_connector.py

Implements DatabaseConnector for REST APIs.
An API endpoint is treated as a "table" — paginated GET = stream_rows,
POST/PUT = bulk_insert.

Use cases:
    REST API  → Database  (migrate API data into a DB)
    Database  → REST API  (push migrated data to an API)
    REST API  → REST API  (API-to-API migration with transformation)

config:
    base_url:      "https://api.example.com/v1"
    auth_type:     "none" | "bearer" | "basic" | "api_key"
    auth_token:    Bearer token (if auth_type="bearer")
    api_key:       API key value (if auth_type="api_key")
    api_key_header: Header name for API key (default: "X-API-Key")
    username:      Basic auth username
    password:      Basic auth password
    headers:       Additional headers dict

    # For stream_rows (source):
    list_endpoint:    "/customers"       → GET {base_url}/customers
    pagination_type:  "offset" | "cursor" | "page" | "link"
    page_param:       "page" (for page-based)
    offset_param:     "offset" (for offset-based)
    limit_param:      "limit"
    limit:            100  (items per page)
    cursor_param:     "cursor" (for cursor-based)
    next_link_key:    "next" (JSON key with next page URL)
    data_key:         "data" | "results" | "items" (where rows live in response)
    id_field:         "id" (field used as PK)

    # For bulk_insert (target):
    create_endpoint:  "/customers"       → POST {base_url}/customers
    create_method:    "POST" | "PUT"
    batch_endpoint:   "/customers/bulk"  → POST with array (if API supports batch)
    batch_size:       10 (items per API call)

Requirements:
    pip install requests
"""

import time
from typing import Dict, Any, List, Generator, Optional

from backend.connector_framework.base.base_connector import (
    DatabaseConnector, ConnectorCapabilities, SchemaInfo, BulkWriteResult
)
from backend.shared.config.logging import logger


class RestApiConnector(DatabaseConnector):

    @property
    def name(self) -> str:
        return "rest_api"

    @property
    def display_name(self) -> str:
        return f"REST API ({self.config.get('base_url', '')})"

    @property
    def capabilities(self) -> ConnectorCapabilities:
        return ConnectorCapabilities(
            discover=True, stream_read=True, bulk_write=True,
            cdc=False, checksum=False, constraints=False,
            indexes=False, jsonb=True, partitioning=False,
        )

    def connect(self) -> None:
        import requests
        self._session = requests.Session()
        self._apply_auth(self._session)
        extra_headers = self.config.get("headers", {})
        self._session.headers.update(extra_headers)
        logger.info("RestApiConnector connected", base_url=self.config.get("base_url"))

    def disconnect(self) -> None:
        if self._session:
            self._session.close()
            self._session = None

    def test_connection(self) -> Dict[str, Any]:
        start = time.time()
        try:
            self.connect()
            base_url = self.config.get("base_url", "")
            # Try a HEAD or GET on base URL
            resp = self._session.get(base_url, timeout=10)
            return {
                "success":    resp.status_code < 400,
                "db_version": f"REST API ({resp.status_code})",
                "latency_ms": int((time.time() - start) * 1000),
                "error":      None if resp.status_code < 400 else f"HTTP {resp.status_code}",
            }
        except Exception as e:
            return {
                "success": False, "db_version": None,
                "latency_ms": int((time.time() - start) * 1000), "error": str(e),
            }
        finally:
            self.disconnect()

    def discover_schema(self) -> SchemaInfo:
        """
        Discover schema by fetching one page of data and inferring types
        from the first record. Returns a minimal SchemaInfo.
        """
        base_url = self.config.get("base_url", "")
        endpoint = self.config.get("list_endpoint", "/")
        data_key = self.config.get("data_key", "data")
        id_field = self.config.get("id_field", "id")
        tables   = {}

        try:
            url  = base_url.rstrip("/") + endpoint
            resp = self._session.get(url, params={
                self.config.get("limit_param", "limit"): 1
            }, timeout=30)
            resp.raise_for_status()
            body = resp.json()

            # Extract records
            records = body
            if isinstance(body, dict):
                records = body.get(data_key) or body.get("results") or body.get("items") or []
            if not isinstance(records, list):
                records = [records] if records else []

            if records:
                sample = records[0]
                columns = {}
                for key, val in sample.items():
                    if isinstance(val, bool):    sql_type = "boolean"
                    elif isinstance(val, int):   sql_type = "bigint"
                    elif isinstance(val, float): sql_type = "double"
                    elif isinstance(val, dict):  sql_type = "jsonb"
                    elif isinstance(val, list):  sql_type = "jsonb"
                    else:                        sql_type = "text"

                    columns[key] = {
                        "type": sql_type, "nullable": True,
                        "pk": key == id_field, "unique": key == id_field,
                        "default": None, "extra": "",
                    }

                table_name = endpoint.strip("/").replace("/", "_") or "api_data"
                tables[table_name] = {
                    "columns":      columns,
                    "primary_keys": [id_field] if id_field in columns else [],
                    "foreign_keys": [],
                    "indexes":      [],
                    "row_count":    self._get_total_count(body),
                    "endpoint":     endpoint,
                }

        except Exception as e:
            logger.warning("REST API schema discovery failed", error=str(e))

        return SchemaInfo(database=self.config.get("base_url", ""), engine="rest_api", tables=tables)

    def get_row_count(self, table_name: str) -> int:
        try:
            base_url = self.config.get("base_url", "")
            endpoint = self.config.get("list_endpoint", f"/{table_name}")
            resp     = self._session.get(
                base_url.rstrip("/") + endpoint,
                params={self.config.get("limit_param", "limit"): 1},
                timeout=30
            )
            return self._get_total_count(resp.json())
        except Exception:
            return 0

    def get_avg_row_size(self, table_name: str) -> int:
        return 1024   # Typical JSON record

    def stream_rows(
        self, table_name, pk_column, pk_start, pk_end,
        columns=None, batch_size=100
    ) -> Generator[Dict[str, Any], None, None]:
        """
        Stream all records from a paginated API endpoint.
        PK range filtering is applied client-side since REST APIs
        typically don't support range queries on arbitrary fields.
        """
        base_url   = self.config.get("base_url", "")
        endpoint   = self.config.get("list_endpoint", f"/{table_name}")
        data_key   = self.config.get("data_key", "data")
        pagination = self.config.get("pagination_type", "offset")
        limit      = min(batch_size, self.config.get("limit", 100))
        limit_param = self.config.get("limit_param", "limit")

        url    = base_url.rstrip("/") + endpoint
        offset = 0
        page   = 1
        cursor = None

        while True:
            params = {limit_param: limit}

            if pagination == "offset":
                params[self.config.get("offset_param", "offset")] = offset
            elif pagination == "page":
                params[self.config.get("page_param", "page")] = page
            elif pagination == "cursor" and cursor:
                params[self.config.get("cursor_param", "cursor")] = cursor

            try:
                resp = self._session.get(url, params=params, timeout=60)
                resp.raise_for_status()
                body = resp.json()
            except Exception as e:
                logger.warning("REST API fetch failed", error=str(e))
                break

            # Extract records from response
            records = body
            if isinstance(body, dict):
                records = (body.get(data_key) or body.get("results") or
                           body.get("items") or [])

            if not records:
                break

            for record in records:
                if isinstance(record, dict):
                    # Apply PK range filter if possible
                    pk_val = record.get(pk_column)
                    if pk_val is not None:
                        try:
                            if not (pk_start <= pk_val <= pk_end):
                                continue
                        except TypeError:
                            pass   # Non-comparable — yield all
                    if columns:
                        record = {k: v for k, v in record.items() if k in columns}
                    yield record

            # Check for next page
            if pagination == "link" and isinstance(body, dict):
                next_url = body.get(self.config.get("next_link_key", "next"))
                if next_url:
                    url    = next_url
                    params = {}   # next URL is complete
                else:
                    break
            elif pagination == "cursor" and isinstance(body, dict):
                cursor = body.get("next_cursor") or body.get("cursor")
                if not cursor:
                    break
            elif len(records) < limit:
                break   # Last page
            else:
                offset += limit
                page   += 1

    def bulk_insert(self, table_name, rows, mode="ignore_duplicates") -> BulkWriteResult:
        """
        POST rows to the API endpoint.
        Supports individual POSTs or batch endpoint if configured.
        Respects configured rate limits.
        """
        if not rows:
            return BulkWriteResult(0, 0, 0, 0)

        base_url       = self.config.get("base_url", "")
        endpoint       = self.config.get("create_endpoint", f"/{table_name}")
        method         = self.config.get("create_method", "POST").upper()
        batch_endpoint = self.config.get("batch_endpoint")
        batch_size     = self.config.get("batch_size", 1)
        rate_limit_ms  = self.config.get("rate_limit_ms", 0)

        start     = time.time()
        inserted  = 0
        failed    = 0
        url       = base_url.rstrip("/") + (batch_endpoint or endpoint)

        if batch_endpoint and batch_size > 1:
            # Batch mode: send chunks
            for i in range(0, len(rows), batch_size):
                batch = rows[i:i+batch_size]
                try:
                    resp = self._session.request(method, url, json=batch, timeout=30)
                    if resp.status_code < 400:
                        inserted += len(batch)
                    else:
                        failed += len(batch)
                except Exception:
                    failed += len(batch)
                if rate_limit_ms:
                    time.sleep(rate_limit_ms / 1000)
        else:
            # Individual records
            for row in rows:
                try:
                    resp = self._session.request(method, url, json=row, timeout=30)
                    if resp.status_code < 400:
                        inserted += 1
                    else:
                        failed += 1
                except Exception:
                    failed += 1
                if rate_limit_ms:
                    time.sleep(rate_limit_ms / 1000)

        elapsed = int((time.time() - start) * 1000)
        return BulkWriteResult(inserted, 0, failed, elapsed,
                               f"{failed} API calls failed" if failed else None)

    def count_rows_in_range(self, table_name, pk_column, pk_start, pk_end) -> int:
        return sum(1 for _ in self.stream_rows(table_name, pk_column, pk_start, pk_end))

    def compute_checksum(self, table_name, pk_column, pk_start, pk_end) -> str:
        import hashlib
        h = hashlib.md5()
        for row in self.stream_rows(table_name, pk_column, pk_start, pk_end):
            h.update(str(sorted(row.items())).encode())
        return h.hexdigest()[:16]

    # ── Private ───────────────────────────────────────────────────────────────

    def _apply_auth(self, session) -> None:
        auth_type = self.config.get("auth_type", "none").lower()
        if auth_type == "bearer":
            session.headers["Authorization"] = f"Bearer {self.config.get('auth_token', '')}"
        elif auth_type == "basic":
            session.auth = (self.config.get("username", ""),
                            self.config.get("password", ""))
        elif auth_type == "api_key":
            header = self.config.get("api_key_header", "X-API-Key")
            session.headers[header] = self.config.get("api_key", "")

    def _get_total_count(self, body: Any) -> int:
        if isinstance(body, dict):
            for key in ("total", "count", "total_count", "totalCount", "total_results"):
                if key in body:
                    try:
                        return int(body[key])
                    except (ValueError, TypeError):
                        pass
        return 0
