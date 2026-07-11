"""
Simulation Engine — FastAPI Application
File: migration/backend/simulation/main.py

Part 5: Simulation Engine. Runs on port 8011.

What-if calculator for migration parameters. Projects duration, throughput,
CPU usage, network consumption, target storage, failure probability, and
bottleneck identification — without connecting to any production database.

Uses real statistics from Metadata Catalog (Part 3) when available.
Falls back to formula-based estimates for manual input.

Start:
    cd migration/
    uvicorn backend.simulation.main:app --host 0.0.0.0 --port 8011 --reload

Docs: http://localhost:8011/docs

ALL ENDPOINTS:

── SIMULATION ────────────────────────────────────────────────────────────────
    POST /simulate                  Single scenario simulation
    POST /simulate/compare          Multi-scenario comparison
    POST /simulate/worker-sweep     Auto-sweep worker counts (2→4→8→16→32)
    GET  /simulate/runs             List saved simulation runs
    GET  /simulate/runs/{id}        Get one saved run
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.simulation.routers import simulation

app = FastAPI(
    title="Migration Platform — Simulation Engine",
    description=(
        "Part 5: What-if calculator for migration parameters. "
        "Projects duration, throughput, CPU usage, network consumption, "
        "target storage, failure probability, and bottleneck identification "
        "without touching any production database. "
        "Supports single-scenario simulation, multi-scenario comparison, "
        "and automatic worker-count sweep to find optimal configuration."
    ),
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(simulation.router)


@app.on_event("startup")
def on_startup():
    from backend.shared.config.database import SessionLocal
    from backend.shared.config.logging import logger

    db = SessionLocal()
    try:
        try:
            from backend.kernel.service_registry.service_registry import ServiceRegistry
            ServiceRegistry.register(
                db=db,
                service_name="simulation_engine",
                display_name="Simulation Engine",
                base_url="http://localhost:8011",
                version="1.0.0",
                metadata={
                    "part": 5,
                    "endpoints": [
                        "POST /simulate",
                        "POST /simulate/compare",
                        "POST /simulate/worker-sweep",
                    ],
                },
            )
        except Exception as e:
            logger.warning("Could not register with Service Registry", error=str(e))

        logger.info("Simulation Engine started", port=8011)
    finally:
        db.close()


@app.get("/health", tags=["Health"])
def health():
    return {
        "status":  "ok",
        "service": "simulation_engine",
        "port":    8011,
        "version": "1.0.0",
        "capabilities": [
            "single_simulation",
            "scenario_comparison",
            "worker_sweep",
            "bottleneck_detection",
            "failure_probability",
        ],
    }
