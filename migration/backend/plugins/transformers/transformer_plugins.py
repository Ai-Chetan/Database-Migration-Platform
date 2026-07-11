"""
Transformer Plugins
File: migration/backend/plugins/transformers/transformer_plugins.py

Refactors the existing TransformationEngine into kernel plugins.
Each transformer handles one mapping_kind and registers with PluginManager.

Existing mapping_kinds from schema_mapping_service/transformation_engine:
    direct      → copy value as-is
    rename      → copy from differently-named source column
    transform   → apply a Python expression to the value
    constant    → always write a fixed literal
    expression  → compute from multiple source columns
    lookup      → replace value from a lookup table
    mask        → Part 7 masking (delegates to MaskingEngine)
    synthesize  → Part 7 synthetic data (delegates to SyntheticGenerator)

Each TransformerPlugin.apply(value, row, config) → transformed_value.
The TransformNode (Part 2) dispatches to the right plugin by mapping_kind.

Adding a new transformer (e.g. "geo_normalize"):
    1. Subclass TransformerPlugin
    2. Implement apply()
    3. PluginManager.register(PluginType.TRANSFORMER, "geo_normalize", GeoNormalizeTransformer)
    Zero changes to existing code.
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, Optional


class TransformerPlugin(ABC):
    """Base class for all transformer plugins."""

    name:         str = "base_transformer"
    display_name: str = "Base Transformer"
    mapping_kind: str = "direct"

    def __init__(self, config: Dict[str, Any] = None):
        self.config = config or {}

    @abstractmethod
    def apply(
        self,
        value:          Any,
        row:            Dict[str, Any],
        mapping_config: Dict[str, Any],
    ) -> Any:
        """
        Transform a single value.
        value:          the source column value
        row:            the full source row (for multi-column transformations)
        mapping_config: the mapping_config JSONB from schema_column_mappings
        Returns the transformed value.
        """


# ── Built-in transformers ─────────────────────────────────────────────────────

class DirectTransformer(TransformerPlugin):
    """Copy value as-is. Identity transformation."""
    name = "direct_transformer"; display_name = "Direct Copy"; mapping_kind = "direct"

    def apply(self, value, row, mapping_config) -> Any:
        return value


class RenameTransformer(TransformerPlugin):
    """Copy from a differently-named source column."""
    name = "rename_transformer"; display_name = "Rename Column"; mapping_kind = "rename"

    def apply(self, value, row, mapping_config) -> Any:
        source_col = mapping_config.get("source_column")
        if source_col and source_col in row:
            return row[source_col]
        return value


class ConstantTransformer(TransformerPlugin):
    """Always write a fixed literal, ignoring the source value."""
    name = "constant_transformer"; display_name = "Constant Value"; mapping_kind = "constant"

    def apply(self, value, row, mapping_config) -> Any:
        return mapping_config.get("value")


class ExpressionTransformer(TransformerPlugin):
    """
    Compute a value from a Python expression. The expression has access
    to `value` (source value) and `row` (full source row dict).

    config: {"expression": "row['first_name'] + ' ' + row['last_name']"}
    SECURITY: expressions are admin-defined only, not user-supplied.
    """
    name = "expression_transformer"; display_name = "Python Expression"; mapping_kind = "expression"

    def apply(self, value, row, mapping_config) -> Any:
        expr = mapping_config.get("expression", "value")
        try:
            return eval(expr, {"__builtins__": {}}, {"value": value, "row": row})
        except Exception as e:
            raise ValueError(f"Expression '{expr}' failed: {e}")


class TransformTransformer(TransformerPlugin):
    """
    Apply a named transformation function to the value.
    config: {"function": "upper" | "lower" | "strip" | "int" | "float" | "str" |
             "date_format" | "truncate"}
    """
    name = "transform_transformer"; display_name = "Transform Function"; mapping_kind = "transform"

    _FUNCTIONS = {
        "upper":   lambda v, _: str(v).upper() if v is not None else None,
        "lower":   lambda v, _: str(v).lower() if v is not None else None,
        "strip":   lambda v, _: str(v).strip() if v is not None else None,
        "int":     lambda v, _: int(v) if v is not None else None,
        "float":   lambda v, _: float(v) if v is not None else None,
        "str":     lambda v, _: str(v) if v is not None else None,
        "bool":    lambda v, _: bool(v) if v is not None else None,
        "truncate": lambda v, c: str(v)[:c.get("max_length", 255)] if v is not None else None,
        "date_format": lambda v, c: _reformat_date(v, c.get("format", "%Y-%m-%d")),
        "coalesce": lambda v, c: v if v is not None else c.get("default"),
        "abs":      lambda v, _: abs(v) if v is not None else None,
        "round":    lambda v, c: round(float(v), c.get("decimals", 2)) if v is not None else None,
    }

    def apply(self, value, row, mapping_config) -> Any:
        fn_name = mapping_config.get("function", "str")
        fn      = self._FUNCTIONS.get(fn_name)
        if not fn:
            raise ValueError(f"Unknown transform function '{fn_name}'")
        return fn(value, mapping_config)


class LookupTransformer(TransformerPlugin):
    """
    Replace the value by looking it up in a static mapping dict.
    config: {"lookup": {"M": "Male", "F": "Female", "U": "Unknown"},
             "default": null, "case_sensitive": false}
    """
    name = "lookup_transformer"; display_name = "Lookup/Map Values"; mapping_kind = "lookup"

    def apply(self, value, row, mapping_config) -> Any:
        lookup         = mapping_config.get("lookup", {})
        default        = mapping_config.get("default", value)
        case_sensitive = mapping_config.get("case_sensitive", False)

        if value is None:
            return default

        key = str(value)
        if not case_sensitive:
            key        = key.lower()
            lookup_adj = {k.lower(): v for k, v in lookup.items()}
        else:
            lookup_adj = lookup

        return lookup_adj.get(key, default)


class MaskTransformer(TransformerPlugin):
    """Delegates to MaskingEngine (Part 7). mapping_kind='mask'."""
    name = "mask_transformer"; display_name = "Data Masking"; mapping_kind = "mask"

    def apply(self, value, row, mapping_config) -> Any:
        try:
            from backend.masking.masking_engine.masking_engine import MaskingEngine
            return MaskingEngine().apply_mask_rule(value, mapping_config)
        except ImportError:
            from backend.masking.strategies.masking_strategies import apply_mask
            return apply_mask(value, mapping_config.get("strategy", "hash"), mapping_config)


class SynthesizeTransformer(TransformerPlugin):
    """Delegates to SyntheticGenerator (Part 7). mapping_kind='synthesize'."""
    name = "synthesize_transformer"; display_name = "Synthetic Data"; mapping_kind = "synthesize"

    def apply(self, value, row, mapping_config) -> Any:
        try:
            from backend.masking.masking_engine.masking_engine import MaskingEngine
            return MaskingEngine().apply_synthesize_rule(value, row, mapping_config)
        except ImportError:
            return f"SYNTHETIC_{hash(str(value)) % 100000:05d}"


# ── Helper ────────────────────────────────────────────────────────────────────

def _reformat_date(value, fmt: str) -> Optional[str]:
    if value is None:
        return None
    import datetime
    if isinstance(value, (datetime.date, datetime.datetime)):
        return value.strftime(fmt)
    # Try parsing common formats
    for parse_fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%Y%m%d"):
        try:
            return datetime.datetime.strptime(str(value), parse_fmt).strftime(fmt)
        except (ValueError, TypeError):
            continue
    return str(value)


# ── Registration ──────────────────────────────────────────────────────────────

def register_all_transformers():
    """Register all built-in transformers with the PluginManager."""
    try:
        from backend.kernel.plugin_manager.plugin_manager import PluginManager, PluginType
        for cls in [DirectTransformer, RenameTransformer, ConstantTransformer,
                    ExpressionTransformer, TransformTransformer, LookupTransformer,
                    MaskTransformer, SynthesizeTransformer]:
            PluginManager.register(
                plugin_type=PluginType.TRANSFORMER,
                name=cls.mapping_kind,
                plugin_class=cls,
                display_name=cls.display_name,
                is_builtin=True,
            )
        from backend.shared.config.logging import logger
        logger.info("Transformer plugins registered", count=8)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Failed to register transformers: {e}")


def get_transformer(mapping_kind: str, config: Dict = None) -> TransformerPlugin:
    """Get a transformer plugin instance by mapping_kind."""
    try:
        from backend.kernel.plugin_manager.plugin_manager import PluginManager, PluginType
        return PluginManager.get(PluginType.TRANSFORMER, mapping_kind, config)
    except Exception:
        # Fallback map for when PluginManager isn't available
        _MAP = {
            "direct": DirectTransformer, "rename": RenameTransformer,
            "constant": ConstantTransformer, "expression": ExpressionTransformer,
            "transform": TransformTransformer, "lookup": LookupTransformer,
            "mask": MaskTransformer, "synthesize": SynthesizeTransformer,
        }
        cls = _MAP.get(mapping_kind, DirectTransformer)
        return cls(config or {})
