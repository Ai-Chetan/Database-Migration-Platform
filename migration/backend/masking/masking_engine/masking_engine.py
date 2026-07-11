"""
Masking Engine
File: migration/backend/masking/masking_engine/masking_engine.py

Orchestrates masking and synthetic data generation at the row level.
Called by the DataMaskingNode (workflow node) during chunk execution.

Two modes:
  mask       → transform real values using a masking strategy
  synthesize → replace real values with deterministic fake data

Integration with existing TransformNode:
  The existing TransformNode in Part 2 handles 6 mapping_kind values:
    direct | rename | transform | constant | expression | lookup
  Part 7 adds 2 more mapping_kind values:
    mask      → delegate to MaskingEngine.apply_mask_rule()
    synthesize → delegate to MaskingEngine.apply_synthesize_rule()

  The TransformNode's _apply() dispatch already has an extension point
  for unknown mapping_kind values. This engine provides the implementation.

Usage from TransformNode (Part 2):
    if mapping.mapping_kind == "mask":
        result = MaskingEngine.apply_mask_rule(value, mapping.mapping_config)
    elif mapping.mapping_kind == "synthesize":
        result = MaskingEngine.apply_synthesize_rule(value, row, mapping.mapping_config)

Standalone batch usage:
    engine = MaskingEngine()
    masked_rows = engine.apply_to_batch(rows, masking_rules)
"""

import datetime
import uuid
from typing import Dict, Any, List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import text

from backend.masking.strategies.masking_strategies import apply_mask
from backend.masking.synthetic.synthetic_generator import SyntheticGenerator
from backend.shared.config.logging import logger


class MaskingEngine:

    def apply_mask_rule(
        self,
        value:  Any,
        config: Dict[str, Any],
    ) -> Any:
        """
        Apply one mask rule to one value.
        config = {"strategy": "hash", "algorithm": "sha256"}
        config = {"strategy": "partial", "keep_start": 2, "keep_end": 4}
        """
        strategy = config.get("strategy", "hash")
        return apply_mask(value, strategy, config)

    def apply_synthesize_rule(
        self,
        value:  Any,
        row:    Dict[str, Any],
        config: Dict[str, Any],
    ) -> Any:
        """
        Apply one synthesize rule to one value.
        config = {"generator": "fake_email", "locale": "en_US", "seed_column": "id"}
        """
        generator  = config.get("generator", "fake_text")
        locale     = config.get("locale", "en_US")
        seed_col   = config.get("seed_column")
        seed_value = row.get(seed_col, value) if seed_col else value

        gen = SyntheticGenerator(locale=locale)
        return gen.generate(generator, seed_value, config)

    def apply_to_batch(
        self,
        rows:     List[Dict[str, Any]],
        rules:    List[Dict[str, Any]],
        # rules: [{"column_name": "email", "mapping_kind": "mask",
        #          "mapping_config": {"strategy": "hash"}}]
    ) -> List[Dict[str, Any]]:
        """
        Apply masking/synthesizing rules to an entire batch of rows.
        Used by DataMaskingNode and standalone testing.
        Returns new list of dicts with masked values.
        """
        if not rules:
            return rows

        # Pre-separate mask vs synthesize rules for clarity
        mask_rules      = {r["column_name"]: r for r in rules if r.get("mapping_kind") == "mask"}
        synthesize_rules = {r["column_name"]: r for r in rules if r.get("mapping_kind") == "synthesize"}

        result = []
        for row in rows:
            masked_row = dict(row)

            # Apply mask rules
            for col, rule in mask_rules.items():
                if col in masked_row:
                    config = rule.get("mapping_config", {})
                    masked_row[col] = self.apply_mask_rule(masked_row[col], config)

            # Apply synthesize rules
            for col, rule in synthesize_rules.items():
                if col in masked_row:
                    config = rule.get("mapping_config", {})
                    masked_row[col] = self.apply_synthesize_rule(
                        masked_row[col], row, config
                    )

            result.append(masked_row)

        return result

    def load_rules_for_table(
        self,
        db:          Session,
        table_name:  str,
        rule_set_id: Optional[str] = None,
        job_id:      Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Load masking rules for a table from the DB.
        Priority: job-specific mapping rules → rule set rules → none
        """
        rules = []

        # 1. Load from schema_column_mappings (job-specific, mapping_kind=mask/synthesize)
        if job_id:
            rows = db.execute(
                text("""
                    SELECT scm.source_column AS column_name,
                           scm.mapping_kind,
                           scm.mapping_config
                    FROM schema_column_mappings scm
                    JOIN schema_table_mappings stm ON scm.table_mapping_id = stm.id
                    JOIN mapping_projects mp ON stm.project_id = mp.id
                    WHERE scm.source_table = :tname
                    AND scm.mapping_kind IN ('mask', 'synthesize')
                    AND scm.is_active = TRUE
                """),
                {"tname": table_name}
            ).fetchall()

            for row in rows:
                mc = row.mapping_config
                if isinstance(mc, str):
                    import json
                    try:
                        mc = json.loads(mc)
                    except Exception:
                        mc = {}
                rules.append({
                    "column_name":   row.column_name,
                    "mapping_kind":  row.mapping_kind,
                    "mapping_config": mc or {},
                })

        # 2. Load from masking_rules table (rule set)
        if rule_set_id and not rules:
            rows = db.execute(
                text("""
                    SELECT column_name, strategy, strategy_config
                    FROM masking_rules
                    WHERE rule_set_id = :rsid
                    AND table_name = :tname
                    AND is_active = TRUE
                """),
                {"rsid": rule_set_id, "tname": table_name}
            ).fetchall()

            for row in rows:
                sc = row.strategy_config
                if isinstance(sc, str):
                    import json
                    try:
                        sc = json.loads(sc)
                    except Exception:
                        sc = {}
                config = {**(sc or {}), "strategy": row.strategy}
                rules.append({
                    "column_name":   row.column_name,
                    "mapping_kind":  "mask",
                    "mapping_config": config,
                })

        return rules

    def log_masking(
        self,
        db:          Session,
        job_id:      str,
        table_name:  str,
        rules_applied: List[Dict],
        rows_masked:  int,
        rows_skipped: int,
    ) -> None:
        """Log masking activity to masking_job_log."""
        now = datetime.datetime.utcnow()
        for rule in rules_applied:
            try:
                db.execute(
                    text("""
                        INSERT INTO masking_job_log
                            (id, job_id, table_name, column_name, strategy,
                             rows_masked, rows_skipped, applied_at)
                        VALUES
                            (:id, :jid, :tname, :col, :strat,
                             :masked, :skipped, :now)
                    """),
                    {
                        "id":      str(uuid.uuid4()),
                        "jid":     job_id,
                        "tname":   table_name,
                        "col":     rule.get("column_name", ""),
                        "strat":   rule.get("mapping_config", {}).get("strategy",
                                   rule.get("mapping_kind", "mask")),
                        "masked":  rows_masked,
                        "skipped": rows_skipped,
                        "now":     now,
                    }
                )
            except Exception:
                pass
        try:
            db.commit()
        except Exception:
            db.rollback()
