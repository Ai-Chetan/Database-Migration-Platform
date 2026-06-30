"""
Connectors Router
File: migration/backend/connector_framework/routers/connectors.py

Endpoints:
    GET  /connectors                    → list all registered connectors
    GET  /connectors/{name}             → get connector detail + capabilities
    POST /connectors/test               → test a connection config
    POST /connectors/validate-config    → validate config fields
    POST /connectors/discover           → discover schema via connector
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional, Dict, Any, List

from backend.shared.config.database import get_db
from backend.connector_framework.registry.connector_registry import ConnectorRegistry

router = APIRouter(prefix="/connectors", tags=["Connector Framework"])


class TestConnectionRequest(BaseModel):
    engine:   str
    host:     str
    port:     int
    database: str
    user:     str
    password: str
    ssl_enabled: bool = False
    connect_timeout: int = 10


class ValidateConfigRequest(BaseModel):
    engine: str
    config: Dict[str, Any]


class DiscoverRequest(BaseModel):
    engine:   str
    host:     str
    port:     int
    database: str
    user:     str
    password: str
    pg_schema: Optional[str] = "public"


@router.get("", summary="List all registered connector plugins")
def list_connectors():
    """
    Returns all registered database connectors with their capabilities.

    Response:
    [
      {
        "name": "mysql",
        "display_name": "MySQL",
        "capabilities": {
          "discover": true, "stream_read": true, "bulk_write": true,
          "cdc": true, "checksum": true, "constraints": true,
          "indexes": true, "jsonb": false
        }
      },
      {
        "name": "postgresql",
        "display_name": "PostgreSQL",
        "capabilities": { ... "cdc": true, "jsonb": true }
      },
      {
        "name": "sqlite",
        "display_name": "SQLite",
        "capabilities": { ... "cdc": false }
      }
    ]

    To add a new connector: implement DatabaseConnector, call
    ConnectorRegistry.register("name", MyConnector) at startup.
    """
    return ConnectorRegistry.list_connectors()


@router.get("/{connector_name}", summary="Get connector detail and capabilities")
def get_connector(connector_name: str):
    """Get detail for one connector by name."""
    connectors = ConnectorRegistry.list_connectors()
    match = next((c for c in connectors if c["name"] == connector_name.lower()), None)
    if not match:
        raise HTTPException(
            status_code=404,
            detail=f"Connector '{connector_name}' not found. "
                   f"Available: {[c['name'] for c in connectors]}"
        )
    return match


@router.post("/test", summary="Test a database connection using the connector framework")
def test_connection(req: TestConnectionRequest):
    """
    Test a database connection using the registered connector.
    Returns connection latency, DB version, and success/error.

    This uses the connector plugin rather than raw driver code,
    so it validates that the full connector stack works end-to-end.
    """
    config = req.dict()
    config["engine"] = req.engine
    try:
        connector = ConnectorRegistry.get(req.engine, config)
        result    = connector.test_connection()
        return {
            "connector": req.engine,
            "host":      req.host,
            "database":  req.database,
            **result,
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        return {
            "connector": req.engine,
            "success":   False,
            "error":     str(e),
            "latency_ms": 0,
        }


@router.post("/validate-config", summary="Validate connector config fields")
def validate_config(req: ValidateConfigRequest):
    """
    Validate that a config dict has all required fields for a connector.

    Response:
    {
      "valid": false,
      "missing": ["password"],
      "warnings": ["SSL not enabled — recommended for production"]
    }
    """
    result = ConnectorRegistry.validate_config(req.engine, req.config)
    return {"engine": req.engine, **result}


@router.post("/discover", summary="Discover schema from a live database via connector")
def discover_schema(req: DiscoverRequest):
    """
    Connect to a live database and discover its full schema.
    Uses the registered connector plugin for the given engine.

    Returns the same schema format used throughout the platform:
    tables, columns, types, PKs, FKs, indexes, row counts.

    This endpoint is a direct test of the connector's discover_schema()
    without saving to the schema_versions table.
    To save the result, use POST /schemas/discover in the schema mapping service.
    """
    config = req.dict()
    config["engine"] = req.engine
    try:
        connector = ConnectorRegistry.get(req.engine, config)
        with connector:
            schema = connector.discover_schema()
        return {
            "engine":      req.engine,
            "database":    schema.database,
            "table_count": len(schema.tables),
            "tables":      schema.tables,
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Discovery failed: {e}")
