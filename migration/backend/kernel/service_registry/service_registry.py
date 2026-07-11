"""
Service Registry
File: migration/backend/kernel/service_registry/service_registry.py

Tracks all microservices in the platform — their base URL, health status,
version. Replaces hardcoded port references (8000, 8001, 8003, 8004, 8005,
8006...) scattered across services and, eventually, the Frontend / API Gateway.

Two halves:
    1. Registration/lookup — simple CRUD on service_registry table.
    2. Health checking — a background thread periodically GETs each
       service's /health endpoint and updates status.

This becomes especially valuable once Part 10 (Kubernetes) puts an API
Gateway in front of everything — the gateway can use this table to route,
and the Operations Console (Part 8) can show a live "which services are up"
dashboard using the same data.

Usage:
    from backend.kernel.service_registry.service_registry import ServiceRegistry

    # Register at service startup
    ServiceRegistry.register(db, "worker_service", "Worker Service",
                              "http://localhost:8002")

    # Look up another service's URL instead of hardcoding it
    url = ServiceRegistry.get_url(db, "schema_mapping_service")
    requests.post(f"{url}/projects", json=...)

    # Health check loop (call once at kernel service startup)
    ServiceRegistry.start_health_checker(interval_seconds=30)
"""

import datetime
import threading
import time
from typing import Optional, Dict, Any, List
from sqlalchemy.orm import Session
from sqlalchemy import text

from backend.shared.config.logging import logger

try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False


class ServiceRegistry:

    _health_thread: Optional[threading.Thread] = None
    _health_running: bool = False

    # ── Registration ──────────────────────────────────────────────────────────

    @classmethod
    def register(
        cls,
        db:              Session,
        service_name:    str,
        display_name:    str,
        base_url:        str,
        health_endpoint: str = "/health",
        version:         str = "1.0.0",
        metadata:        Optional[Dict[str, Any]] = None,
    ) -> dict:
        """Register or update a service's entry. Idempotent (upsert)."""
        import json
        now = datetime.datetime.utcnow()

        db.execute(
            text("""
                INSERT INTO service_registry
                    (service_name, display_name, base_url, health_endpoint,
                     version, status, metadata, registered_at, updated_at)
                VALUES
                    (:name, :dname, :url, :health, :version, 'unknown',
                     CAST(:meta AS jsonb), :now, :now)
                ON CONFLICT (service_name)
                DO UPDATE SET
                    display_name    = :dname,
                    base_url        = :url,
                    health_endpoint = :health,
                    version         = :version,
                    metadata        = CAST(:meta AS jsonb),
                    updated_at      = :now
            """),
            {
                "name": service_name, "dname": display_name, "url": base_url,
                "health": health_endpoint, "version": version,
                "meta": json.dumps(metadata or {}), "now": now,
            }
        )
        db.commit()
        logger.info("Service registered", service=service_name, url=base_url)
        return cls.get(db, service_name)

    @classmethod
    def deregister(cls, db: Session, service_name: str) -> dict:
        db.execute(
            text("DELETE FROM service_registry WHERE service_name=:name"),
            {"name": service_name}
        )
        db.commit()
        return {"deregistered": service_name}

    # ── Lookup ────────────────────────────────────────────────────────────────

    @classmethod
    def get(cls, db: Session, service_name: str) -> Optional[dict]:
        row = db.execute(
            text("SELECT * FROM service_registry WHERE service_name=:name"),
            {"name": service_name}
        ).fetchone()
        return cls._row(row) if row else None

    @classmethod
    def get_url(cls, db: Session, service_name: str) -> Optional[str]:
        """The most common call: just give me the base URL for this service."""
        row = db.execute(
            text("SELECT base_url FROM service_registry WHERE service_name=:name"),
            {"name": service_name}
        ).fetchone()
        return row[0] if row else None

    @classmethod
    def list_all(cls, db: Session) -> List[dict]:
        rows = db.execute(
            text("SELECT * FROM service_registry ORDER BY service_name")
        ).fetchall()
        return [cls._row(r) for r in rows]

    @classmethod
    def list_healthy(cls, db: Session) -> List[dict]:
        rows = db.execute(
            text("SELECT * FROM service_registry WHERE status='healthy' ORDER BY service_name")
        ).fetchall()
        return [cls._row(r) for r in rows]

    # ── Health checking ───────────────────────────────────────────────────────

    @classmethod
    def check_health(cls, db: Session, service_name: str) -> dict:
        """Check one service's health right now (synchronous, on-demand)."""
        service = cls.get(db, service_name)
        if not service:
            return {"service": service_name, "status": "not_registered"}

        status, error = cls._ping(service["base_url"], service["health_endpoint"])

        db.execute(
            text("""
                UPDATE service_registry
                SET status=:status, last_heartbeat=:now, updated_at=:now
                WHERE service_name=:name
            """),
            {"status": status, "now": datetime.datetime.utcnow(), "name": service_name}
        )
        db.commit()

        return {"service": service_name, "status": status, "error": error}

    @classmethod
    def check_all_health(cls, db: Session) -> List[dict]:
        """Check health of every registered service, synchronously, once."""
        services = cls.list_all(db)
        results = []
        for svc in services:
            results.append(cls.check_health(db, svc["service_name"]))
        return results

    @classmethod
    def start_health_checker(cls, interval_seconds: int = 30) -> None:
        """
        Start a background thread that periodically health-checks every
        registered service. Call once, typically from the kernel service's
        startup. Safe to call multiple times — subsequent calls are no-ops
        if already running.
        """
        if cls._health_running:
            return

        cls._health_running = True

        def _loop():
            from backend.shared.config.database import SessionLocal
            while cls._health_running:
                db = SessionLocal()
                try:
                    cls.check_all_health(db)
                except Exception as e:
                    logger.warning("Service health check loop failed", error=str(e))
                finally:
                    db.close()
                time.sleep(interval_seconds)

        cls._health_thread = threading.Thread(
            target=_loop, daemon=True, name="service-registry-health-checker"
        )
        cls._health_thread.start()
        logger.info("Service Registry health checker started", interval=interval_seconds)

    @classmethod
    def stop_health_checker(cls) -> None:
        cls._health_running = False
        if cls._health_thread:
            cls._health_thread.join(timeout=5)

    # ── Private ───────────────────────────────────────────────────────────────

    @classmethod
    def _ping(cls, base_url: str, health_endpoint: str) -> (str, Optional[str]):
        if not REQUESTS_AVAILABLE:
            return "unknown", "requests library not installed"
        try:
            url = base_url.rstrip("/") + health_endpoint
            resp = requests.get(url, timeout=5)
            if resp.status_code == 200:
                return "healthy", None
            return "degraded", f"HTTP {resp.status_code}"
        except Exception as e:
            return "down", str(e)

    @classmethod
    def _row(cls, row) -> dict:
        if not row:
            return {}
        d = dict(row._mapping)
        for k, v in d.items():
            if hasattr(v, "hex"):        d[k] = str(v)
            if hasattr(v, "isoformat"):  d[k] = v.isoformat()
        return d
