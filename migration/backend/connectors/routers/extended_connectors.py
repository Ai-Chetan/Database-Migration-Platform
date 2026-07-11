"""
Extended Connectors Router
File: migration/backend/connectors/routers/extended_connectors.py

Endpoints:
    GET  /connectors/extended                    → list extended connectors
    POST /connectors/file/test                   → test a file connector config
    POST /connectors/file/discover               → discover schema from files
    POST /connectors/object-storage/test         → test object storage connection
    POST /connectors/object-storage/list-objects → list objects in bucket
    POST /connectors/api/test                    → test REST API connection
    POST /connectors/api/discover                → discover API schema
    POST /connectors/kafka/test                  → test Kafka connection
    POST /connectors/kafka/topics                → list Kafka topics
    POST /connectors/kafka/sample                → sample messages from a topic
    POST /connectors/register                    → register a new extended connector
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, List, Dict, Any

from backend.shared.config.logging import logger

router = APIRouter(prefix="/connectors/extended", tags=["Extended Connectors"])


# ── Request models ─────────────────────────────────────────────────────────────

class FileTestRequest(BaseModel):
    database:  str          # directory path
    format:    str = "csv"  # csv | tsv | excel | json | parquet | avro

class ObjectStorageTestRequest(BaseModel):
    provider:           str          # s3 | azure | gcs
    bucket:             str
    access_key:         Optional[str] = None
    secret_key:         Optional[str] = None
    region:             Optional[str] = None
    endpoint_url:       Optional[str] = None
    connection_string:  Optional[str] = None
    account_name:       Optional[str] = None
    account_key:        Optional[str] = None

class ObjectStorageListRequest(BaseModel):
    provider:    str
    bucket:      str
    prefix:      str = ""
    access_key:  Optional[str] = None
    secret_key:  Optional[str] = None
    region:      Optional[str] = None
    connection_string: Optional[str] = None

class ApiTestRequest(BaseModel):
    base_url:   str
    auth_type:  str = "none"
    auth_token: Optional[str] = None
    api_key:    Optional[str] = None
    username:   Optional[str] = None
    password:   Optional[str] = None
    headers:    Optional[Dict[str, str]] = None

class ApiDiscoverRequest(BaseModel):
    base_url:        str
    list_endpoint:   str = "/"
    auth_type:       str = "none"
    auth_token:      Optional[str] = None
    api_key:         Optional[str] = None
    data_key:        str = "data"
    id_field:        str = "id"
    headers:         Optional[Dict[str, str]] = None

class KafkaTestRequest(BaseModel):
    bootstrap_servers: str
    topic:             Optional[str] = None

class KafkaSampleRequest(BaseModel):
    bootstrap_servers:  str
    topic:              str
    max_messages:       int = 10
    auto_offset_reset:  str = "earliest"
    timeout_ms:         int = 10000

class RegisterConnectorRequest(BaseModel):
    engine:        str      # "file" | "object_storage" | "rest_api" | "kafka"
    display_name:  str
    config:        Dict[str, Any]
    test_first:    bool = True


# ── Discovery ──────────────────────────────────────────────────────────────────

@router.get("", summary="List all extended connector types")
def list_extended_connectors():
    """
    Returns all extended connector types added in Part 9.
    These complement the SQL connectors (MySQL, PostgreSQL, SQLite) from the Connector Framework.
    """
    return {
        "extended_connectors": [
            {
                "engine": "file",
                "display_name": "File Connector",
                "description": "CSV, TSV, Excel, JSON, Parquet, Avro files",
                "formats": ["csv", "tsv", "excel", "json", "parquet", "avro"],
                "cdc": False,
                "install": "pip install pandas openpyxl pyarrow fastavro",
            },
            {
                "engine": "object_storage",
                "display_name": "Object Storage",
                "description": "AWS S3, Azure Blob Storage, Google Cloud Storage",
                "providers": ["s3", "azure", "gcs"],
                "cdc": False,
                "install": "pip install boto3 azure-storage-blob google-cloud-storage",
            },
            {
                "engine": "rest_api",
                "display_name": "REST API",
                "description": "Paginated REST API endpoints (source or target)",
                "auth_types": ["none", "bearer", "basic", "api_key"],
                "pagination_types": ["offset", "cursor", "page", "link"],
                "cdc": False,
                "install": "pip install requests",
            },
            {
                "engine": "kafka",
                "display_name": "Apache Kafka",
                "description": "Kafka topics as source or target",
                "formats": ["json", "avro", "string"],
                "cdc": False,
                "install": "pip install confluent-kafka",
            },
        ]
    }


# ── File connector endpoints ───────────────────────────────────────────────────

@router.post("/file/test", summary="Test a file connector config")
def test_file_connector(req: FileTestRequest):
    """Test connection to a directory of files."""
    from backend.connectors.file.file_connector import FileConnector
    connector = FileConnector({"database": req.database, "format": req.format})
    connector.connect()
    result = connector.test_connection()
    return {"engine": "file", "format": req.format, **result}


@router.post("/file/discover", summary="Discover schema from files in a directory")
def discover_file_schema(req: FileTestRequest):
    """
    Discover schema from all files matching the format in the directory.
    Returns tables (one per file) with inferred column types.
    """
    from backend.connectors.file.file_connector import FileConnector
    try:
        connector = FileConnector({"database": req.database, "format": req.format})
        connector.connect()
        schema = connector.discover_schema()
        return {
            "engine":      "file",
            "database":    schema.database,
            "format":      req.format,
            "table_count": len(schema.tables),
            "tables":      schema.tables,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Discovery failed: {e}")


# ── Object Storage endpoints ───────────────────────────────────────────────────

@router.post("/object-storage/test", summary="Test object storage connection")
def test_object_storage(req: ObjectStorageTestRequest):
    """Test connection to S3, Azure Blob, or GCS."""
    from backend.connectors.object_storage.object_storage_connector import ObjectStorageConnector
    config = req.dict(exclude_none=True)
    config["engine"] = "object_storage"
    try:
        connector = ObjectStorageConnector(config)
        connector.connect()
        result = connector.test_connection()
        return {"engine": "object_storage", "provider": req.provider, **result}
    except Exception as e:
        return {"engine": "object_storage", "provider": req.provider,
                "success": False, "error": str(e)}


@router.post("/object-storage/list-objects", summary="List objects in a bucket/container")
def list_objects(req: ObjectStorageListRequest):
    """List up to 100 objects in the configured bucket/container/prefix."""
    from backend.connectors.object_storage.object_storage_connector import ObjectStorageConnector
    config = req.dict(exclude_none=True)
    config["engine"] = "object_storage"
    try:
        connector = ObjectStorageConnector(config)
        connector.connect()
        objects = connector._list_objects(req.provider, req.bucket, req.prefix)
        return {
            "provider": req.provider,
            "bucket":   req.bucket,
            "prefix":   req.prefix,
            "count":    len(objects),
            "objects":  objects[:100],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Object listing failed: {e}")


# ── REST API endpoints ─────────────────────────────────────────────────────────

@router.post("/api/test", summary="Test a REST API connection")
def test_api_connector(req: ApiTestRequest):
    """Test connectivity to a REST API base URL."""
    from backend.connectors.api.rest_api_connector import RestApiConnector
    config = req.dict(exclude_none=True)
    config["engine"] = "rest_api"
    try:
        connector = RestApiConnector(config)
        connector.connect()
        result = connector.test_connection()
        connector.disconnect()
        return {"engine": "rest_api", **result}
    except Exception as e:
        return {"engine": "rest_api", "success": False, "error": str(e)}


@router.post("/api/discover", summary="Discover schema from a REST API endpoint")
def discover_api_schema(req: ApiDiscoverRequest):
    """
    Fetch one page from a REST API endpoint and infer schema from the response.
    Returns the inferred column types based on the first record.
    """
    from backend.connectors.api.rest_api_connector import RestApiConnector
    config = req.dict(exclude_none=True)
    config["engine"] = "rest_api"
    try:
        connector = RestApiConnector(config)
        connector.connect()
        schema = connector.discover_schema()
        connector.disconnect()
        return {
            "engine":      "rest_api",
            "base_url":    req.base_url,
            "endpoint":    req.list_endpoint,
            "table_count": len(schema.tables),
            "tables":      schema.tables,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"API discovery failed: {e}")


@router.post("/api/sample", summary="Fetch a sample of records from a REST API")
def sample_api(req: ApiDiscoverRequest, limit: int = 5):
    """Fetch up to N records from a REST API endpoint for preview."""
    from backend.connectors.api.rest_api_connector import RestApiConnector
    config = req.dict(exclude_none=True)
    config["engine"] = "rest_api"
    try:
        connector = RestApiConnector(config)
        connector.connect()
        table_name = req.list_endpoint.strip("/").replace("/", "_") or "api_data"
        rows = []
        for row in connector.stream_rows(table_name, "id", 0, 999999999):
            rows.append(row)
            if len(rows) >= limit:
                break
        connector.disconnect()
        return {"endpoint": req.list_endpoint, "sample_count": len(rows), "records": rows}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"API sample failed: {e}")


# ── Kafka endpoints ────────────────────────────────────────────────────────────

@router.post("/kafka/test", summary="Test Kafka broker connection")
def test_kafka(req: KafkaTestRequest):
    """Test connectivity to a Kafka broker and optionally check a topic exists."""
    from backend.connectors.streaming.kafka_connector import KafkaConnector
    config = {"bootstrap_servers": req.bootstrap_servers, "engine": "kafka"}
    if req.topic:
        config["topic"] = req.topic
    try:
        connector = KafkaConnector(config)
        connector.connect()
        result = connector.test_connection()
        return {"engine": "kafka", **result}
    except Exception as e:
        return {"engine": "kafka", "success": False, "error": str(e)}


@router.post("/kafka/topics", summary="List Kafka topics")
def list_kafka_topics(req: KafkaTestRequest):
    """List all non-internal Kafka topics on the broker."""
    try:
        from confluent_kafka.admin import AdminClient
        admin    = AdminClient({"bootstrap.servers": req.bootstrap_servers})
        metadata = admin.list_topics(timeout=10)
        topics   = sorted(t for t in metadata.topics.keys() if not t.startswith("__"))
        return {
            "bootstrap_servers": req.bootstrap_servers,
            "topic_count":       len(topics),
            "topics":            topics,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Topic listing failed: {e}")


@router.post("/kafka/sample", summary="Sample messages from a Kafka topic")
def sample_kafka(req: KafkaSampleRequest):
    """
    Consume up to max_messages from a Kafka topic for preview.
    Uses auto_offset_reset="earliest" by default to sample from the beginning.
    """
    from backend.connectors.streaming.kafka_connector import KafkaConnector
    config = {
        "bootstrap_servers":  req.bootstrap_servers,
        "engine":             "kafka",
        "max_messages":       req.max_messages,
        "consumer_timeout_ms": req.timeout_ms,
        "auto_offset_reset":  req.auto_offset_reset,
        "message_format":     "json",
    }
    try:
        connector = KafkaConnector(config)
        connector.connect()
        rows = []
        for row in connector.stream_rows(req.topic, "_kafka_offset", 0, 999999999):
            rows.append(row)
            if len(rows) >= req.max_messages:
                break
        connector.disconnect()
        return {
            "topic":         req.topic,
            "messages_read": len(rows),
            "sample":        rows,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Kafka sample failed: {e}")


# ── Registration ───────────────────────────────────────────────────────────────

@router.post("/register", summary="Register an extended connector with the platform")
def register_connector(req: RegisterConnectorRequest):
    """
    Register a new extended connector with ConnectorRegistry and PluginManager.

    engine options:
      file           → FileConnector
      object_storage → ObjectStorageConnector
      rest_api       → RestApiConnector
      kafka          → KafkaConnector

    If test_first=true, tests the connection before registering.
    """
    connector_map = {
        "file":           "backend.connectors.file.file_connector.FileConnector",
        "object_storage": "backend.connectors.object_storage.object_storage_connector.ObjectStorageConnector",
        "rest_api":       "backend.connectors.api.rest_api_connector.RestApiConnector",
        "kafka":          "backend.connectors.streaming.kafka_connector.KafkaConnector",
    }

    module_path = connector_map.get(req.engine)
    if not module_path:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown engine '{req.engine}'. Valid: {list(connector_map.keys())}"
        )

    # Test connection if requested
    if req.test_first:
        try:
            parts = module_path.rsplit(".", 1)
            import importlib
            mod  = importlib.import_module(parts[0])
            cls  = getattr(mod, parts[1])
            conn = cls({**req.config, "engine": req.engine})
            conn.connect()
            result = conn.test_connection()
            conn.disconnect()
            if not result.get("success"):
                raise HTTPException(
                    status_code=400,
                    detail=f"Connection test failed: {result.get('error')}"
                )
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Connection test failed: {e}")

    # Register with ConnectorRegistry
    try:
        import importlib
        parts = module_path.rsplit(".", 1)
        mod   = importlib.import_module(parts[0])
        cls   = getattr(mod, parts[1])
        from backend.connector_framework.registry.connector_registry import ConnectorRegistry
        ConnectorRegistry.register(req.engine, cls)
        status = "registered"
    except Exception as e:
        status = f"registration_failed: {e}"

    return {
        "engine":      req.engine,
        "display_name": req.display_name,
        "status":      status,
        "module_path": module_path,
    }
