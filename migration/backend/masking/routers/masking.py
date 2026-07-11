"""
Masking Router
File: migration/backend/masking/routers/masking.py

Endpoints:
    POST /masking/rule-sets                      → create a masking rule set
    GET  /masking/rule-sets                      → list rule sets
    GET  /masking/rule-sets/{id}                 → get rule set detail
    POST /masking/rule-sets/{id}/rules           → add a rule to a rule set
    GET  /masking/rule-sets/{id}/rules           → list rules in a set
    DELETE /masking/rule-sets/{id}/rules/{rule_id} → delete a rule
    POST /masking/preview                        → preview masking on sample data
    GET  /masking/strategies                     → list available masking strategies
    GET  /masking/generators                     → list available synthetic data generators
    GET  /masking/logs/{job_id}                  → get masking activity for a job
    POST /masking/test                           → test a single masking rule
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
import datetime
import uuid

from backend.shared.config.database import get_db
from backend.masking.masking_engine.masking_engine import MaskingEngine
from backend.masking.strategies.masking_strategies import apply_mask, STRATEGY_MAP
from backend.masking.synthetic.synthetic_generator import SyntheticGenerator

router = APIRouter(prefix="/masking", tags=["Data Masking & Synthetic Data"])
engine = MaskingEngine()


# ── Request models ─────────────────────────────────────────────────────────────

class CreateRuleSetRequest(BaseModel):
    name:        str
    description: Optional[str] = None
    tenant_id:   str = "local"


class AddRuleRequest(BaseModel):
    table_name:      str
    column_name:     str
    strategy:        str
    strategy_config: Optional[Dict[str, Any]] = None


class PreviewRequest(BaseModel):
    sample_rows:  List[Dict[str, Any]]
    rules:        List[Dict[str, Any]]
    # [{"column_name": "email", "mapping_kind": "mask",
    #   "mapping_config": {"strategy": "hash"}}]


class TestRuleRequest(BaseModel):
    value:          Any
    mapping_kind:   str = "mask"   # mask | synthesize
    mapping_config: Dict[str, Any]
    row_context:    Optional[Dict[str, Any]] = None


# ── Rule Set endpoints ─────────────────────────────────────────────────────────

@router.post("/rule-sets", summary="Create a masking rule set")
def create_rule_set(req: CreateRuleSetRequest, db: Session = Depends(get_db)):
    """
    Create a named, reusable collection of masking rules.
    Rule sets can be applied to any migration job.

    Once created, add rules via POST /masking/rule-sets/{id}/rules.
    Then apply to a job via the DataMaskingNode config: {"rule_set_id": "..."}.
    """
    rid = str(uuid.uuid4())
    db.execute(
        text("""
            INSERT INTO masking_rule_sets (id, tenant_id, name, description, created_at, updated_at)
            VALUES (:id, :tid, :name, :desc, :now, :now)
        """),
        {"id": rid, "tid": req.tenant_id, "name": req.name,
         "desc": req.description, "now": datetime.datetime.utcnow()}
    )
    db.commit()
    return {"id": rid, "name": req.name, "tenant_id": req.tenant_id}


@router.get("/rule-sets", summary="List masking rule sets")
def list_rule_sets(tenant_id: str = "local", db: Session = Depends(get_db)):
    rows = db.execute(
        text("""
            SELECT rs.id, rs.name, rs.description, rs.is_active, rs.created_at,
                   COUNT(r.id) AS rule_count
            FROM masking_rule_sets rs
            LEFT JOIN masking_rules r ON r.rule_set_id = rs.id
            WHERE rs.tenant_id=:tid
            GROUP BY rs.id ORDER BY rs.created_at DESC
        """),
        {"tid": tenant_id}
    ).fetchall()
    result = []
    for row in rows:
        d = dict(row._mapping)
        for k, v in d.items():
            if hasattr(v, "hex"):        d[k] = str(v)
            if hasattr(v, "isoformat"):  d[k] = v.isoformat()
        result.append(d)
    return result


@router.get("/rule-sets/{rule_set_id}", summary="Get rule set detail")
def get_rule_set(rule_set_id: str, db: Session = Depends(get_db)):
    row = db.execute(
        text("SELECT * FROM masking_rule_sets WHERE id=:id"),
        {"id": rule_set_id}
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"Rule set {rule_set_id} not found")
    d = dict(row._mapping)
    for k, v in d.items():
        if hasattr(v, "hex"):        d[k] = str(v)
        if hasattr(v, "isoformat"):  d[k] = v.isoformat()
    return d


@router.post("/rule-sets/{rule_set_id}/rules", summary="Add a masking rule to a rule set")
def add_rule(rule_set_id: str, req: AddRuleRequest, db: Session = Depends(get_db)):
    """
    Add a column-level masking rule to a rule set.

    strategy options:
      hash            → SHA-256 one-way hash (consistent, join-safe)
      redact          → replace with "***REDACTED***"
      partial         → keep N chars at start/end, mask middle
      encrypt         → AES reversible encryption
      nullify         → replace with NULL
      fixed_value     → replace with a fixed literal
      format_preserve → mask digits/letters, keep separators

    strategy_config examples:
      hash:         {"algorithm": "sha256", "prefix": "USR_"}
      partial:      {"keep_start": 2, "keep_end": 4, "mask_char": "*"}
      fixed_value:  {"value": "MASKED"}
      format_preserve: {"digit_char": "X"}
    """
    # Check rule set exists
    rs = db.execute(
        text("SELECT id FROM masking_rule_sets WHERE id=:id"),
        {"id": rule_set_id}
    ).fetchone()
    if not rs:
        raise HTTPException(status_code=404, detail="Rule set not found")

    valid_strategies = list(STRATEGY_MAP.keys())
    if req.strategy not in valid_strategies:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid strategy '{req.strategy}'. Valid: {valid_strategies}"
        )

    import json
    rid = str(uuid.uuid4())
    db.execute(
        text("""
            INSERT INTO masking_rules
                (id, rule_set_id, table_name, column_name, strategy, strategy_config, created_at)
            VALUES (:id, :rsid, :tname, :col, :strat, :config::jsonb, :now)
        """),
        {
            "id":     rid,
            "rsid":   rule_set_id,
            "tname":  req.table_name,
            "col":    req.column_name,
            "strat":  req.strategy,
            "config": json.dumps(req.strategy_config or {}),
            "now":    datetime.datetime.utcnow(),
        }
    )
    db.commit()
    return {"id": rid, "rule_set_id": rule_set_id,
            "table_name": req.table_name, "column_name": req.column_name,
            "strategy": req.strategy}


@router.get("/rule-sets/{rule_set_id}/rules", summary="List rules in a rule set")
def list_rules(rule_set_id: str, db: Session = Depends(get_db)):
    rows = db.execute(
        text("""
            SELECT id, table_name, column_name, strategy, strategy_config, is_active, created_at
            FROM masking_rules WHERE rule_set_id=:rsid ORDER BY table_name, column_name
        """),
        {"rsid": rule_set_id}
    ).fetchall()
    result = []
    for row in rows:
        d = dict(row._mapping)
        for k, v in d.items():
            if hasattr(v, "hex"):        d[k] = str(v)
            if hasattr(v, "isoformat"):  d[k] = v.isoformat()
        result.append(d)
    return result


@router.delete("/rule-sets/{rule_set_id}/rules/{rule_id}", summary="Delete a masking rule")
def delete_rule(rule_set_id: str, rule_id: str, db: Session = Depends(get_db)):
    db.execute(
        text("DELETE FROM masking_rules WHERE id=:id AND rule_set_id=:rsid"),
        {"id": rule_id, "rsid": rule_set_id}
    )
    db.commit()
    return {"deleted": rule_id}


# ── Preview + Test endpoints ───────────────────────────────────────────────────

@router.post("/preview", summary="Preview masking on sample data")
def preview_masking(req: PreviewRequest):
    """
    Apply masking rules to a small sample of rows and return both the
    original and masked versions side-by-side. No DB writes.

    Useful for validating your masking configuration before running a
    real migration. Safe to call repeatedly with production data samples.

    Request:
    {
      "sample_rows": [
        {"id": 1, "email": "john@example.com", "name": "John Doe", "ssn": "123-45-6789"}
      ],
      "rules": [
        {"column_name": "email", "mapping_kind": "mask",
         "mapping_config": {"strategy": "hash"}},
        {"column_name": "name",  "mapping_kind": "synthesize",
         "mapping_config": {"generator": "fake_name", "seed_column": "id"}},
        {"column_name": "ssn",   "mapping_kind": "mask",
         "mapping_config": {"strategy": "format_preserve"}}
      ]
    }
    """
    masked_rows = engine.apply_to_batch(req.sample_rows, req.rules)

    comparison = []
    for original, masked in zip(req.sample_rows, masked_rows):
        comparison.append({
            "original": original,
            "masked":   masked,
            "changed_columns": [
                k for k in original if original.get(k) != masked.get(k)
            ],
        })

    return {
        "rows_processed": len(comparison),
        "rules_applied":  len(req.rules),
        "comparison":     comparison,
    }


@router.post("/test", summary="Test a single masking rule on one value")
def test_rule(req: TestRuleRequest):
    """
    Quick test of a single masking rule on a single value.
    Returns original and masked value side-by-side.

    Example:
    {
      "value": "john@example.com",
      "mapping_kind": "mask",
      "mapping_config": {"strategy": "partial", "keep_start": 2, "keep_end": 4}
    }
    Response:
    {
      "original": "john@example.com",
      "masked":   "jo***********com",
      "strategy": "partial"
    }
    """
    if req.mapping_kind == "mask":
        result = engine.apply_mask_rule(req.value, req.mapping_config)
    elif req.mapping_kind == "synthesize":
        row = req.row_context or {}
        result = engine.apply_synthesize_rule(req.value, row, req.mapping_config)
    else:
        raise HTTPException(status_code=400,
                            detail="mapping_kind must be 'mask' or 'synthesize'")

    return {
        "original":    req.value,
        "masked":      result,
        "strategy":    req.mapping_config.get("strategy") or req.mapping_config.get("generator"),
        "deterministic": True,
    }


# ── Discovery endpoints ────────────────────────────────────────────────────────

@router.get("/strategies", summary="List available masking strategies")
def list_strategies():
    return {
        "strategies": [
            {"name": "hash",             "description": "SHA-256 one-way hash. Consistent — same input always gives same output. Preserves join-ability.", "reversible": False},
            {"name": "redact",           "description": "Replace with '***REDACTED***' or custom string.", "reversible": False},
            {"name": "partial",          "description": "Keep N chars at start/end, mask middle. Good for emails, phones.", "reversible": False},
            {"name": "encrypt",          "description": "AES encryption. Reversible with platform key.", "reversible": True},
            {"name": "nullify",          "description": "Replace with NULL.", "reversible": False},
            {"name": "fixed_value",      "description": "Replace with a configured literal string.", "reversible": False},
            {"name": "format_preserve",  "description": "Replace alphanumerics, keep separators. '555-123-4567' → '555-XXX-XXXX'.", "reversible": False},
        ]
    }


@router.get("/generators", summary="List available synthetic data generators")
def list_generators():
    return {
        "generators": [
            {"name": "fake_name",        "description": "Full name",               "example": "Alice Smith"},
            {"name": "fake_first_name",  "description": "First name only",         "example": "Alice"},
            {"name": "fake_last_name",   "description": "Last name only",          "example": "Smith"},
            {"name": "fake_email",       "description": "Email address",           "example": "alice.smith@example.com"},
            {"name": "fake_phone",       "description": "Phone number",            "example": "+1-555-867-5309"},
            {"name": "fake_address",     "description": "Full address",            "example": "123 Main St, Springfield, IL"},
            {"name": "fake_city",        "description": "City name",               "example": "Springfield"},
            {"name": "fake_postcode",    "description": "Postal/ZIP code",         "example": "62701"},
            {"name": "fake_company",     "description": "Company name",            "example": "Acme Corporation"},
            {"name": "fake_ssn",         "description": "Social Security Number",  "example": "123-45-6789"},
            {"name": "fake_credit_card", "description": "Credit card number",      "example": "4111111111111111"},
            {"name": "fake_date",        "description": "Random date",             "example": "1985-06-15"},
            {"name": "fake_dob",         "description": "Date of birth",           "example": "1985-06-15"},
            {"name": "fake_username",    "description": "Username",                "example": "alice_smith_42"},
            {"name": "fake_ipv4",        "description": "IPv4 address",            "example": "192.168.1.42"},
            {"name": "fake_url",         "description": "URL",                     "example": "https://example.com/path"},
            {"name": "fake_text",        "description": "Lorem ipsum paragraph",   "example": "Lorem ipsum..."},
            {"name": "fake_integer",     "description": "Random integer",          "example": "42831"},
            {"name": "fake_uuid",        "description": "UUID",                    "example": "550e8400-e29b-41d4-a716-446655440000"},
            {"name": "fake_job",         "description": "Job title",               "example": "Software Engineer"},
        ],
        "note": "All generators are deterministic: same source value → same fake value. "
                "This preserves referential integrity across tables."
    }


# ── Log endpoint ───────────────────────────────────────────────────────────────

@router.get("/logs/{job_id}", summary="Get masking activity for a job")
def get_masking_logs(job_id: str, db: Session = Depends(get_db)):
    rows = db.execute(
        text("""
            SELECT table_name, column_name, strategy, rows_masked, rows_skipped, applied_at
            FROM masking_job_log WHERE job_id=:jid ORDER BY applied_at DESC
        """),
        {"jid": job_id}
    ).fetchall()
    result = []
    for row in rows:
        d = dict(row._mapping)
        for k, v in d.items():
            if hasattr(v, "isoformat"): d[k] = v.isoformat()
        result.append(d)
    total_masked = sum(r["rows_masked"] for r in result)
    return {"job_id": job_id, "total_rows_masked": total_masked,
            "entries": len(result), "logs": result}
