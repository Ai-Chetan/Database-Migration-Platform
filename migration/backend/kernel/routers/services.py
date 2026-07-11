"""
Service Registry Router
File: migration/backend/kernel/routers/services.py

Endpoints:
    POST /services                     → register a microservice
    GET  /services                     → list all registered services
    GET  /services/{name}              → get one service's detail
    GET  /services/{name}/url          → get just the base_url
    POST /services/{name}/health-check → check health right now
    POST /services/health-check-all    → check health of every service
    DELETE /services/{name}            → deregister a service
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional, Dict, Any

from backend.shared.config.database import get_db
from backend.kernel.service_registry.service_registry import ServiceRegistry

router = APIRouter(prefix="/services", tags=["Service Registry"])


class RegisterServiceRequest(BaseModel):
    service_name:    str
    display_name:    str
    base_url:        str
    health_endpoint: str = "/health"
    version:         str = "1.0.0"
    metadata:        Optional[Dict[str, Any]] = None


@router.post("", summary="Register a microservice")
def register_service(req: RegisterServiceRequest, db: Session = Depends(get_db)):
    """
    Register (or update, if it already exists — idempotent) a microservice's
    presence in the platform. Every kernel-aware service should call this
    at startup so others can discover it instead of hardcoding ports.

    Already seeded built-in: control_plane, monitoring_service,
    schema_mapping_service, enterprise_execution, enterprise_security,
    connector_framework_cdc, platform_kernel.
    """
    return ServiceRegistry.register(
        db=db,
        service_name=req.service_name,
        display_name=req.display_name,
        base_url=req.base_url,
        health_endpoint=req.health_endpoint,
        version=req.version,
        metadata=req.metadata,
    )


@router.get("", summary="List all registered services")
def list_services(healthy_only: bool = False, db: Session = Depends(get_db)):
    """
    Lists every microservice in the platform with current status.
    Set healthy_only=true to filter to just the ones currently passing
    health checks — useful for an API Gateway's routing table (Part 10).
    """
    if healthy_only:
        return ServiceRegistry.list_healthy(db)
    return ServiceRegistry.list_all(db)


@router.get("/{service_name}", summary="Get one service's detail")
def get_service(service_name: str, db: Session = Depends(get_db)):
    service = ServiceRegistry.get(db, service_name)
    if not service:
        raise HTTPException(status_code=404, detail=f"Service '{service_name}' not registered")
    return service


@router.get("/{service_name}/url", summary="Get just the base URL for a service")
def get_service_url(service_name: str, db: Session = Depends(get_db)):
    """
    Convenience endpoint: other services can call this instead of
    hardcoding 'http://localhost:8003' etc. Returns a 404 if unregistered.
    """
    url = ServiceRegistry.get_url(db, service_name)
    if not url:
        raise HTTPException(status_code=404, detail=f"Service '{service_name}' not registered")
    return {"service_name": service_name, "base_url": url}


@router.post("/{service_name}/health-check", summary="Check one service's health now")
def check_health(service_name: str, db: Session = Depends(get_db)):
    """
    Synchronously pings the service's /health endpoint right now and
    updates its stored status. Use this for an on-demand check; the
    background health checker (started via ServiceRegistry.start_health_checker())
    does this automatically every 30s.
    """
    return ServiceRegistry.check_health(db, service_name)


@router.post("/health-check-all", summary="Check health of every registered service")
def check_all_health(db: Session = Depends(get_db)):
    """Pings every service's /health endpoint right now. Returns a status list."""
    results = ServiceRegistry.check_all_health(db)
    healthy = sum(1 for r in results if r["status"] == "healthy")
    return {
        "total":   len(results),
        "healthy": healthy,
        "unhealthy": len(results) - healthy,
        "results": results,
    }


@router.delete("/{service_name}", summary="Deregister a service")
def deregister_service(service_name: str, db: Session = Depends(get_db)):
    service = ServiceRegistry.get(db, service_name)
    if not service:
        raise HTTPException(status_code=404, detail=f"Service '{service_name}' not registered")
    return ServiceRegistry.deregister(db, service_name)
