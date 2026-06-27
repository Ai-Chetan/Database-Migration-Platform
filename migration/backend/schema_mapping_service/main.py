"""
Schema Mapping Service — Updated FastAPI Application
File: migration/backend/schema_mapping_service/main.py

REPLACES the Part 1 main.py.

Adds Part 2 routers:
  - validation   → /projects/{id}/validate, /validation-results
  - versioning   → /schemas/versions/list, /schemas/compare-versions, /schemas/{id}/changelog
  - constraints  → /projects/{id}/ddl/create-tables, /indexes, /foreign-keys, /full, /analyze

Start:
    cd migration/
    uvicorn backend.schema_mapping_service.main:app --host 0.0.0.0 --port 8003 --reload

Docs: http://localhost:8003/docs

ALL ENDPOINTS (Part 1 + Part 2):

── SCHEMA DISCOVERY ─────────────────────────────────────────────────────────
    POST /schemas/discover                Live DB schema discovery
    POST /schemas/import-file             Import old text-file format
    GET  /schemas                         List saved schema versions
    GET  /schemas/{id}                    Full schema version data
    GET  /schemas/{id}/tables             Table summary list

── SCHEMA VERSIONING (Part 2) ───────────────────────────────────────────────
    GET  /schemas/versions/list           All versions grouped by name
    POST /schemas/compare-versions        Diff two saved versions
    GET  /schemas/{id}/changelog          Human-readable changelog vs previous

── MAPPING PROJECTS ──────────────────────────────────────────────────────────
    POST /projects                        Create project
    GET  /projects                        List projects
    GET  /projects/{id}                   Project detail
    PUT  /projects/{id}/status            Update status

── TABLE & COLUMN MAPPINGS ──────────────────────────────────────────────────
    POST /projects/{id}/table-mappings              Create mapping (single/split/merge/graph)
    GET  /projects/{id}/table-mappings              List mappings
    DELETE /projects/{id}/table-mappings/{mid}      Delete mapping
    POST /table-mappings/{mid}/column-mappings      Add column mapping
    POST /table-mappings/{mid}/column-mappings/bulk Bulk save
    GET  /table-mappings/{mid}/column-mappings      List column mappings
    DELETE /column-mappings/{cid}                   Delete column mapping

── SCHEMA COMPARISON & TYPE ANALYSIS ────────────────────────────────────────
    POST /compare                         Full schema diff
    POST /analyze-type                    Single type conversion analysis
    POST /analyze-table-types             All column conversions for a table

── RECOMMENDATIONS ───────────────────────────────────────────────────────────
    POST /projects/{id}/recommend               Run auto-recommender
    GET  /projects/{id}/recommendations         List saved recommendations
    POST /projects/{id}/recommendations/accept  Accept
    POST /projects/{id}/recommendations/reject  Reject
    POST /projects/{id}/recommendations/apply   Apply → create mappings

── DRY RUN & PLANNING ────────────────────────────────────────────────────────
    POST /projects/{id}/dry-run           Risk analysis without touching data
    POST /projects/{id}/plan              Generate FK-aware execution plan
    GET  /projects/{id}/plan              Get saved plan

── SCRIPT GENERATION ─────────────────────────────────────────────────────────
    POST /projects/{id}/scripts/generate  Generate Python/SQL/Airflow script
    GET  /projects/{id}/scripts           List generated scripts
    GET  /scripts/{id}/download           Download script content

── VALIDATION (Part 2) ──────────────────────────────────────────────────────
    POST /projects/{id}/validate                  Validate all tables
    POST /projects/{id}/validate/{table}          Validate one table
    GET  /projects/{id}/validation-results        Get saved validation results

── CONSTRAINTS & INDEXES (Part 2) ───────────────────────────────────────────
    POST /projects/{id}/ddl/create-tables  Phase 1: CREATE TABLE DDL
    POST /projects/{id}/ddl/indexes        Phase 3: CREATE INDEX DDL
    POST /projects/{id}/ddl/foreign-keys   Phase 4: ADD FOREIGN KEY DDL
    POST /projects/{id}/ddl/full           All 4 phases combined
    GET  /projects/{id}/ddl/analyze        Detect constraint conflicts
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Part 1 routers
from backend.schema_mapping_service.app.routers import (
    discovery, projects, mappings, comparison, recommendations, planning, scripts
)

# Part 2 routers
from backend.schema_mapping_service.app.routers import validation, versioning, constraints

app = FastAPI(
    title="Migration Platform — Schema Mapping Service",
    description=(
        "Priority 9 — Schema-aware migration engine (Part 1 + Part 2). "
        "Discover schemas, compare structures, define single/split/merge/graph mappings, "
        "auto-recommend column mappings with fuzzy matching, analyze type safety, "
        "run dry-runs, generate execution plans, validate migrated data, "
        "manage schema versioning, generate CREATE TABLE / INDEX / FK DDL, "
        "and produce Python/SQL/Airflow migration scripts."
    ),
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Part 1 routers ─────────────────────────────────────────────────────────────
app.include_router(discovery.router)
app.include_router(projects.router)
app.include_router(mappings.router)
app.include_router(comparison.router)
app.include_router(recommendations.router)
app.include_router(planning.router)
app.include_router(scripts.router)

# ── Part 2 routers ─────────────────────────────────────────────────────────────
app.include_router(validation.router)
app.include_router(versioning.router)
app.include_router(constraints.router)


@app.get("/health", tags=["Health"])
def health():
    return {
        "status":  "ok",
        "service": "schema_mapping_service",
        "port":    8003,
        "version": "2.0.0 (Part 1 + Part 2)",
    }
