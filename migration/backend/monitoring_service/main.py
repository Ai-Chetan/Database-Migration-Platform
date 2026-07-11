"""
Monitoring Service — FastAPI Application
File: migration/backend/monitoring_service/main.py

Independent FastAPI service. Run it separately from the worker.

Start:
    cd migration/
    uvicorn backend.monitoring_service.main:app --host 0.0.0.0 --port 8001 --reload

Interactive docs:
    http://localhost:8001/docs

All endpoints:
    GET  /health
    GET  /jobs
    GET  /jobs/{job_id}
    GET  /jobs/{job_id}/tables
    GET  /jobs/{job_id}/chunks
    GET  /jobs/{job_id}/chunks?status=failed
    GET  /jobs/{job_id}/metrics
    GET  /workers
    GET  /metrics
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.monitoring_service.app.routers import jobs, chunks, workers, metrics

app = FastAPI(
    title="Migration Platform — Monitoring API",
    description="Real-time visibility into migration jobs, chunks, workers, and performance metrics.",
    version="1.0.0",
)

# Allow all origins for now — tighten for production
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

# Register all routers
app.include_router(jobs.router)
app.include_router(chunks.router)
app.include_router(workers.router)
app.include_router(metrics.router)


@app.get("/health", tags=["Health"])
def health():
    """Simple health check — returns 200 if service is up."""
    return {"status": "ok", "service": "monitoring_service"}
