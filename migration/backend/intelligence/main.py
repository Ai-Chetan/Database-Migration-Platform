"""
Metadata Intelligence Layer — FastAPI Application
File: migration/backend/intelligence/main.py

Part 3: Metadata Intelligence Layer. Runs on port 8009.

This service sits between Schema Discovery (port 8003) and the
Intelligence Service / Assessment Engine (Part 4, port 8010).

It answers the question: "What do we actually know about this database?"
Not just the schema — the actual data characteristics: how many rows,
how big, how fast is it growing, are there LOB columns, is the data
skewed, are there broken FK references?

All results are stored in the Metadata Catalog (Part 1, port 8007/table)
and are immediately available to:
  - Assessment Engine (Part 4) — builds complexity/risk reports
  - Migration Advisor (Part 4) — makes data-aware recommendations
  - Cost Estimator (Part 4) — uses real sizes, not guesses
  - Adaptive Chunk Planner (Phase 10) — uses real row sizes and PK stats
  - Simulation Engine (Part 5) — uses growth rates for ETA projection

Start:
    cd migration/
    uvicorn backend.intelligence.main:app --host 0.0.0.0 --port 8009 --reload

Docs: http://localhost:8009/docs

ALL ENDPOINTS:

── INTELLIGENCE SCANS ────────────────────────────────────────────────────────
    POST /intelligence/scans                Start a full metadata scan
    GET  /intelligence/scans/{id}           Get scan status and progress
    GET  /intelligence/scans               List all scans

── TABLE INTELLIGENCE ────────────────────────────────────────────────────────
    GET  /intelligence/tables/{table}       All catalog data for one table
    GET  /intelligence/tables/{table}/{type} Specific catalog type for a table

── SCHEMA SUMMARY ────────────────────────────────────────────────────────────
    GET  /intelligence/summary             Schema-level aggregated summary
    GET  /intelligence/stale               Tables needing re-scan (TTL expired)

What each scan collects (written to metadata_catalog):

  catalog_type = statistics
    row_count, size_bytes, size_gb, avg_row_bytes, index_size_bytes,
    pk_min, pk_max, pk_fill_ratio, storage_engine, has_auto_increment

  catalog_type = growth_rate
    rows_per_day, rows_per_month, projected_rows_90d, projected_size_90d_gb

  catalog_type = relationship
    cardinality (1:1 / 1:N / 1:N high fan-out), avg_children_per_parent,
    max_children, orphan_count, orphan_pct

  catalog_type = distribution
    per-column: null_pct, distinct_count, top_values (categorical),
    min/max/avg/stddev (numeric), is_skewed, skew_ratio

  catalog_type = lob_detection
    has_lob, lob_columns, per-column avg/max/total size, recommendation

  catalog_type = compression
    is_compressed, compression_method, compression_ratio,
    estimated_uncompressed_bytes
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.intelligence.routers import intelligence

app = FastAPI(
    title="Migration Platform — Metadata Intelligence Layer",
    description=(
        "Part 3: Collects deep table-level intelligence from source databases. "
        "Runs four collectors (Statistics, Relationship Mapper, Distribution Analyzer, "
        "LOB/Compression Detector) and writes all results to the Metadata Catalog. "
        "Feeds the Assessment Engine, Migration Advisor, Cost Estimator, "
        "Adaptive Chunk Planner, and Simulation Engine."
    ),
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(intelligence.router)


@app.on_event("startup")
def on_startup():
    from backend.shared.config.database import SessionLocal
    from backend.shared.config.logging import logger

    db = SessionLocal()
    try:
        # Register with Service Registry
        try:
            from backend.kernel.service_registry.service_registry import ServiceRegistry
            ServiceRegistry.register(
                db=db,
                service_name="intelligence_service",
                display_name="Metadata Intelligence Layer",
                base_url="http://localhost:8009",
                version="1.0.0",
                metadata={
                    "part": 3,
                    "collectors": [
                        "statistics_collector",
                        "relationship_mapper",
                        "distribution_analyzer",
                        "lob_compression_detector",
                    ],
                    "catalog_types": [
                        "statistics", "growth_rate", "relationship",
                        "distribution", "lob_detection", "compression",
                    ],
                },
            )
        except Exception as e:
            logger.warning("Could not register with Service Registry", error=str(e))

        logger.info("Metadata Intelligence Layer started", port=8009)
    finally:
        db.close()


@app.get("/health", tags=["Health"])
def health():
    return {
        "status":  "ok",
        "service": "intelligence_service",
        "port":    8009,
        "version": "1.0.0",
        "collectors": [
            "StatisticsCollector",
            "RelationshipMapper",
            "DistributionAnalyzer",
            "LOBCompressionDetector",
        ],
    }
