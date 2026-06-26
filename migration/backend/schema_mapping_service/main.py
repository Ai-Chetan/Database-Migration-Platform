"""
Schema Mapping Service — FastAPI Application
File: migration/backend/schema_mapping_service/main.py

Priority 9 microservice. Runs independently on port 8003.

Start:
    cd migration/
    uvicorn backend.schema_mapping_service.main:app --host 0.0.0.0 --port 8003 --reload

Interactive docs:
    http://localhost:8003/docs

All endpoints:

SCHEMA DISCOVERY
    POST /schemas/discover              Connect to live DB and save schema
    POST /schemas/import-file           Import from old tool's text file format
    GET  /schemas                       List all saved schema versions
    GET  /schemas/{id}                  Full schema version with all table/column data
    GET  /schemas/{id}/tables           Table summary list

MAPPING PROJECTS
    POST /projects                      Create project (links source + target schema)
    GET  /projects                      List all projects
    GET  /projects/{id}                 Project detail with dry-run and plan
    PUT  /projects/{id}/status          Update project status

TABLE & COLUMN MAPPINGS
    POST /projects/{id}/table-mappings              Create single/split/merge/graph mapping
    GET  /projects/{id}/table-mappings              List table mappings for project
    DELETE /projects/{id}/table-mappings/{mid}      Delete table mapping
    POST /table-mappings/{mid}/column-mappings      Add one column mapping
    POST /table-mappings/{mid}/column-mappings/bulk Bulk save column mappings
    GET  /table-mappings/{mid}/column-mappings      List column mappings
    DELETE /column-mappings/{cid}                   Delete column mapping

SCHEMA COMPARISON & TYPE ANALYSIS
    POST /compare                       Full schema diff (added/removed/renamed/changed)
    POST /analyze-type                  Analyze single type conversion safety
    POST /analyze-table-types           Analyze all column conversions for a table

RECOMMENDATIONS
    POST /projects/{id}/recommend               Run auto-recommender
    GET  /projects/{id}/recommendations         List saved recommendations
    POST /projects/{id}/recommendations/accept  Accept recommendations
    POST /projects/{id}/recommendations/reject  Reject recommendations
    POST /projects/{id}/recommendations/apply   Apply accepted → create mappings

DRY RUN & PLANNING
    POST /projects/{id}/dry-run         Analyze risk, estimate duration, list unsafe conversions
    POST /projects/{id}/plan            Generate ordered FK-aware execution plan
    GET  /projects/{id}/plan            Get saved plan

SCRIPT GENERATION
    POST /projects/{id}/scripts/generate    Generate Python / SQL / Airflow script
    GET  /projects/{id}/scripts             List generated scripts
    GET  /scripts/{id}/download             Download script content
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.schema_mapping_service.app.routers import (
    discovery, projects, mappings, comparison, recommendations, planning, scripts
)

app = FastAPI(
    title="Migration Platform — Schema Mapping Service",
    description=(
        "Priority 9: Schema-aware migration engine. "
        "Discover schemas, compare structures, define mappings (single/split/merge/graph), "
        "auto-recommend column mappings, analyze type safety, run dry-runs, "
        "generate execution plans, and produce Python/SQL/Airflow migration scripts."
    ),
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(discovery.router)
app.include_router(projects.router)
app.include_router(mappings.router)
app.include_router(comparison.router)
app.include_router(recommendations.router)
app.include_router(planning.router)
app.include_router(scripts.router)


@app.get("/health", tags=["Health"])
def health():
    return {"status": "ok", "service": "schema_mapping_service", "port": 8003}
