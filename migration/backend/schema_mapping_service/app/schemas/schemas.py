"""
API Schemas (Pydantic)
File: migration/backend/schema_mapping_service/app/schemas/schemas.py
"""

from pydantic import BaseModel
from typing import List, Optional, Dict, Any


# ── Schema Discovery ──────────────────────────────────────────────────────────

class DiscoverRequest(BaseModel):
    tenant_id:     str = "local"
    name:          str
    version_label: Optional[str] = None
    notes:         Optional[str] = None
    config: Dict[str, Any]   # {engine, host, port, database, user, password}


class FileImportRequest(BaseModel):
    tenant_id:     str = "local"
    name:          str
    version_label: Optional[str] = None
    notes:         Optional[str] = None
    file_content:  str           # raw text content of schema file


class SchemaVersionResponse(BaseModel):
    id:            str
    tenant_id:     str
    name:          str
    db_type:       str
    version_label: Optional[str]
    source_type:   str
    notes:         Optional[str]
    created_at:    str
    table_count:   Optional[int] = None

    class Config:
        from_attributes = True


# ── Mapping Projects ──────────────────────────────────────────────────────────

class CreateProjectRequest(BaseModel):
    tenant_id:        str = "local"
    name:             str
    description:      Optional[str] = None
    source_schema_id: str
    target_schema_id: str


class ProjectResponse(BaseModel):
    id:               str
    tenant_id:        str
    name:             str
    description:      Optional[str]
    source_schema_id: Optional[str]
    target_schema_id: Optional[str]
    status:           str
    created_at:       str
    updated_at:       str

    class Config:
        from_attributes = True


# ── Table Mappings ────────────────────────────────────────────────────────────

class CreateTableMappingRequest(BaseModel):
    mapping_type:   str = "single"      # single | split | merge | graph
    source_tables:  List[str]           # ["users"] or ["users","profiles"]
    target_tables:  List[str]           # ["customers"] or ["cust","cust_addr"]
    join_condition: Optional[str] = None
    notes:          Optional[str] = None


class TableMappingResponse(BaseModel):
    id:             str
    project_id:     str
    mapping_type:   str
    source_tables:  List[str]
    target_tables:  List[str]
    join_condition: Optional[str]
    notes:          Optional[str]
    created_at:     str

    class Config:
        from_attributes = True


# ── Column Mappings ───────────────────────────────────────────────────────────

class CreateColumnMappingRequest(BaseModel):
    source_table:   str
    source_column:  str
    source_type:    str
    target_table:   str
    target_column:  str
    target_type:    str
    mapping_kind:   str = "direct"    # direct|rename|transform|constant|expression|lookup
    mapping_config: Optional[Dict[str, Any]] = None
    # These are computed by the type engine, but can be overridden
    conversion_safety: Optional[str] = None
    requires_cast:     bool = False
    cast_expression:   Optional[str] = None


class BulkColumnMappingRequest(BaseModel):
    mappings: List[CreateColumnMappingRequest]


class ColumnMappingResponse(BaseModel):
    id:               str
    table_mapping_id: str
    source_table:     str
    source_column:    str
    source_type:      str
    target_table:     str
    target_column:    str
    target_type:      str
    mapping_kind:     str
    mapping_config:   Optional[Dict]
    conversion_safety: Optional[str]
    requires_cast:    bool
    cast_expression:  Optional[str]
    created_at:       str

    class Config:
        from_attributes = True


# ── Recommendations ───────────────────────────────────────────────────────────

class RecommendationResponse(BaseModel):
    id:         str    # "source_ref→target_ref"
    rec_type:   str
    source_ref: str
    target_ref: str
    confidence: float
    reason:     str
    accepted:   Optional[bool]


class AcceptRecommendationsRequest(BaseModel):
    rec_ids: List[str]   # ["users→customers", "users.id→customers.id"]


# ── Schema Comparison ─────────────────────────────────────────────────────────

class CompareRequest(BaseModel):
    source_schema_id: str
    target_schema_id: str
    column_mappings:  Optional[Dict[str, Dict[str, str]]] = None


# ── Type Analysis ─────────────────────────────────────────────────────────────

class TypeAnalysisRequest(BaseModel):
    source_type: str
    target_type: str
    source_db:   str = "mysql"
    target_db:   str = "mysql"
    col_ref:     str = "col"


class TypeAnalysisResponse(BaseModel):
    source_type:    str
    target_type:    str
    safety:         str
    requires_cast:  bool
    cast_expression: Optional[str]
    notes:          Optional[str]
    action:         str


# ── Dry Run + Plan ────────────────────────────────────────────────────────────

class DryRunRequest(BaseModel):
    project_id: str


class GeneratePlanRequest(BaseModel):
    project_id: str


# ── Script Generation ─────────────────────────────────────────────────────────

class GenerateScriptRequest(BaseModel):
    project_id:   str
    table_name:   str
    script_type:  str = "python"     # python | sql | airflow_dag
    source_config: Optional[Dict[str, Any]] = None
    target_config: Optional[Dict[str, Any]] = None


class ScriptListResponse(BaseModel):
    id:           str
    script_type:  str
    target_table: str
    filename:     str
    created_at:   str
