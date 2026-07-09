"""
Extended Connector Family — FastAPI Application
File: migration/backend/connectors/main.py

Part 9: Extended Connector Family. Runs on port 8015.

Adds four new connector types to the platform, all implementing the
existing DatabaseConnector interface — zero changes to workers,
WorkflowExecutor, ReadNode, WriteNode, or any existing code.

New connectors:
  FileConnector          → CSV, TSV, Excel, JSON, Parquet, Avro
  ObjectStorageConnector → AWS S3, Azure Blob, Google Cloud Storage
  RestApiConnector       → Paginated REST APIs (source or target)
  KafkaConnector         → Apache Kafka topics (producer or consumer)

All four auto-register with ConnectorRegistry at startup.
After startup, any job can use engine="file", "object_storage",
"rest_api", or "kafka" in connection configs.

Start:
    cd migration/
    uvicorn backend.connectors.main:app --host 0.0.0.0 --port 8015 --reload

Install connector dependencies:
    pip install pandas openpyxl pyarrow fastavro    # File connector
    pip install boto3                                # S3
    pip install azure-storage-blob                  # Azure Blob
    pip install google-cloud-storage                # GCS
    pip install requests                             # REST API
    pip install confluent-kafka                      # Kafka

Docs: http://localhost:8015/docs

ALL ENDPOINTS:

── DISCOVERY ─────────────────────────────────────────────────────────────────
    GET  /connectors/extended                         List extended connector types

── FILE CONNECTOR ────────────────────────────────────────────────────────────
    POST /connectors/extended/file/test               Test file directory
    POST /connectors/extended/file/discover           Discover schema from files

── OBJECT STORAGE ────────────────────────────────────────────────────────────
    POST /connectors/extended/object-storage/test         Test S3/Azure/GCS
    POST /connectors/extended/object-storage/list-objects List bucket objects

── REST API ──────────────────────────────────────────────────────────────────
    POST /connectors/extended/api/test                Test API connection
    POST /connectors/extended/api/discover            Infer schema from endpoint
    POST /connectors/extended/api/sample              Sample records

── KAFKA ─────────────────────────────────────────────────────────────────────
    POST /connectors/extended/kafka/test              Test broker connection
    POST /connectors/extended/kafka/topics            List topics
    POST /connectors/extended/kafka/sample            Sample messages

── REGISTRATION ──────────────────────────────────────────────────────────────
    POST /connectors/extended/register                Register a connector
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.connectors.routers import extended_connectors

app = FastAPI(
    title="Migration Platform — Extended Connector Family",
    description=(
        "Part 9: Four new connector types implementing the DatabaseConnector interface. "
        "FileConnector (CSV/Excel/JSON/Parquet), "
        "ObjectStorageConnector (S3/Azure/GCS), "
        "RestApiConnector (paginated REST APIs), "
        "KafkaConnector (Kafka topics as source or target). "
        "All register with ConnectorRegistry at startup — zero changes to existing workers."
    ),
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(extended_connectors.router)


@app.on_event("startup")
def on_startup():
    from backend.shared.config.logging import logger

    registered = []

    # Register FileConnector
    try:
        from backend.connectors.file.file_connector import FileConnector
        from backend.connector_framework.registry.connector_registry import ConnectorRegistry
        ConnectorRegistry.register("file", FileConnector)
        ConnectorRegistry.register("csv",     FileConnector)
        ConnectorRegistry.register("parquet", FileConnector)
        ConnectorRegistry.register("excel",   FileConnector)
        registered.append("file/csv/parquet/excel")
    except Exception as e:
        logger.warning("FileConnector registration failed", error=str(e))

    # Register ObjectStorageConnector
    try:
        from backend.connectors.object_storage.object_storage_connector import ObjectStorageConnector
        from backend.connector_framework.registry.connector_registry import ConnectorRegistry
        ConnectorRegistry.register("object_storage", ObjectStorageConnector)
        ConnectorRegistry.register("s3",    ObjectStorageConnector)
        ConnectorRegistry.register("azure", ObjectStorageConnector)
        ConnectorRegistry.register("gcs",   ObjectStorageConnector)
        registered.append("s3/azure/gcs")
    except Exception as e:
        logger.warning("ObjectStorageConnector registration failed", error=str(e))

    # Register RestApiConnector
    try:
        from backend.connectors.api.rest_api_connector import RestApiConnector
        from backend.connector_framework.registry.connector_registry import ConnectorRegistry
        ConnectorRegistry.register("rest_api", RestApiConnector)
        ConnectorRegistry.register("api",      RestApiConnector)
        registered.append("rest_api")
    except Exception as e:
        logger.warning("RestApiConnector registration failed", error=str(e))

    # Register KafkaConnector
    try:
        from backend.connectors.streaming.kafka_connector import KafkaConnector
        from backend.connector_framework.registry.connector_registry import ConnectorRegistry
        ConnectorRegistry.register("kafka", KafkaConnector)
        registered.append("kafka")
    except Exception as e:
        logger.warning("KafkaConnector registration failed", error=str(e))

    # Sync to Plugin Manager catalog
    try:
        from backend.kernel.plugin_manager.plugin_manager import PluginManager, PluginType
        from backend.shared.config.database import SessionLocal
        db = SessionLocal()
        try:
            for engine in ["file", "object_storage", "rest_api", "kafka"]:
                PluginManager.register(
                    plugin_type=PluginType.CONNECTOR,
                    name=engine,
                    plugin_class=type(f"{engine}_connector", (), {}),
                    display_name=engine.replace("_", " ").title(),
                    is_builtin=True,
                )
            PluginManager.sync_to_catalog(db)
        finally:
            db.close()
    except Exception as e:
        logger.warning("PluginManager sync failed", error=str(e))

    # Register with Service Registry
    try:
        from backend.kernel.service_registry.service_registry import ServiceRegistry
        from backend.shared.config.database import SessionLocal
        db = SessionLocal()
        try:
            ServiceRegistry.register(
                db=db,
                service_name="extended_connectors",
                display_name="Extended Connector Family",
                base_url="http://localhost:8015",
                version="1.0.0",
                metadata={
                    "part": 9,
                    "connectors": registered,
                },
            )
        finally:
            db.close()
    except Exception as e:
        logger.warning("Service Registry registration failed", error=str(e))

    logger.info("Extended Connector Family started",
                port=8015, registered=registered)


@app.get("/health", tags=["Health"])
def health():
    # Check which optional dependencies are available
    available = {}
    for pkg, label in [
        ("pandas",         "file_csv_json"),
        ("pyarrow",        "file_parquet"),
        ("openpyxl",       "file_excel"),
        ("fastavro",       "file_avro"),
        ("boto3",          "s3"),
        ("azure.storage",  "azure_blob"),
        ("google.cloud",   "gcs"),
        ("requests",       "rest_api"),
        ("confluent_kafka", "kafka"),
    ]:
        try:
            __import__(pkg.split(".")[0])
            available[label] = True
        except ImportError:
            available[label] = False

    return {
        "status":     "ok",
        "service":    "extended_connectors",
        "port":       8015,
        "version":    "1.0.0",
        "connectors": ["file", "object_storage", "rest_api", "kafka"],
        "dependencies": available,
    }
