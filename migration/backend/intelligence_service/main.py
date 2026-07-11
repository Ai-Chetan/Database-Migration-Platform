"""
Intelligence Service — FastAPI Application
File: migration/backend/intelligence_service/main.py

Part 4: Intelligence Service. Runs on port 8010.

This service is READ ONLY. It never connects to source/target databases
directly (except the Data Quality Scanner, which reads but never writes).
It never modifies migration state. Its only job is to analyze and advise.

The four engines inside:
  AssessmentEngine  → complexity/risk/duration/worker-count report
  MigrationAdvisor  → data-aware recommendations (PII, CDC, chunk, index)
  CostEstimator     → cloud compute/storage/network cost projection
  DataQualityScanner→ pre-migration data integrity checks

Start:
    cd migration/
    uvicorn backend.intelligence_service.main:app --host 0.0.0.0 --port 8010 --reload

Docs: http://localhost:8010/docs

ALL ENDPOINTS:

── ASSESSMENT ────────────────────────────────────────────────────────────────
    POST /assess                         Generate assessment report
    GET  /assess/reports                 List saved reports for a connection
    GET  /assess/reports/{id}            Get a saved assessment report

── MIGRATION ADVISOR ─────────────────────────────────────────────────────────
    POST /advise                         Get data-aware migration advice

── COST ESTIMATOR ────────────────────────────────────────────────────────────
    POST /estimate                       Estimate cost and duration

── DATA QUALITY SCANNER ──────────────────────────────────────────────────────
    POST /quality/scan-table             Scan one table for quality issues
    POST /quality/scan-all               Scan all tables
    GET  /quality/results                Get saved quality results
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.intelligence_service.routers import intelligence_service

app = FastAPI(
    title="Migration Platform — Intelligence Service",
    description=(
        "Part 4: Pre-migration intelligence. Assessment Engine generates "
        "complexity/risk/duration reports. Migration Advisor gives data-aware "
        "recommendations (PII detection, CDC advice, chunk strategy, index gaps). "
        "Cost Estimator projects cloud compute/storage/network costs. "
        "Data Quality Scanner finds duplicate PKs, broken FKs, NULL violations, "
        "oversized values, and invalid dates before migration starts. "
        "READ ONLY — never modifies migration state."
    ),
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(intelligence_service.router)


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
                service_name="intelligence_service_v2",
                display_name="Intelligence Service (Assessment + Advisor + Estimator + Scanner)",
                base_url="http://localhost:8010",
                version="1.0.0",
                metadata={
                    "part": 4,
                    "read_only": True,
                    "engines": [
                        "AssessmentEngine",
                        "MigrationAdvisor",
                        "CostEstimator",
                        "DataQualityScanner",
                    ],
                },
            )
        except Exception as e:
            logger.warning("Could not register with Service Registry", error=str(e))

        logger.info("Intelligence Service started", port=8010)
    finally:
        db.close()


@app.get("/health", tags=["Health"])
def health():
    return {
        "status":  "ok",
        "service": "intelligence_service",
        "port":    8010,
        "version": "1.0.0",
        "read_only": True,
        "engines": [
            "AssessmentEngine",
            "MigrationAdvisor",
            "CostEstimator",
            "DataQualityScanner",
        ],
    }
