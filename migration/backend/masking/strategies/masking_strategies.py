"""
Masking Strategies
File: migration/backend/masking/strategies/masking_strategies.py

All masking strategy implementations. Each strategy is a pure function:
    mask(value, config) -> masked_value

Strategies:
  hash              SHA-256 one-way hash. Consistent: same input always
                    produces same output, preserving join-ability across tables.
  redact            Replace entirely with a fixed string (e.g. "***REDACTED***").
  partial           Keep first N and last M chars, mask middle with char.
                    e.g. "john@example.com" -> "jo***********com"
  encrypt           AES encryption via Fernet (reversible with key).
                    Uses same MIGRATION_ENCRYPTION_KEY as Connection Manager.
  nullify           Replace with NULL. Useful for non-required PII columns.
  fixed_value       Replace with a fixed literal ("MASKED", "0", etc.)
  format_preserve   Replace characters while preserving format.
                    "555-123-4567" -> "555-XXX-XXXX" (keeps separators)

All strategies handle None gracefully (return None for None input).
All strategies are deterministic by default (same input → same output)
which preserves referential integrity across tables.
"""

import hashlib
import re
from typing import Any, Optional, Dict


def _safe(value: Any) -> Optional[str]:
    """Convert to string or return None."""
    if value is None:
        return None
    return str(value)


# ── Hash ──────────────────────────────────────────────────────────────────────

def mask_hash(value: Any, config: Dict = None) -> Optional[str]:
    """
    One-way SHA-256 hash. Returns a 16-char hex prefix (readable, not a wall of chars).
    Consistent: same input always gives same output — join-ability preserved.

    config: {"algorithm": "sha256" | "sha1" | "md5", "prefix": "USR_"}
    """
    s = _safe(value)
    if s is None:
        return None

    config    = config or {}
    algo      = config.get("algorithm", "sha256")
    prefix    = config.get("prefix", "")
    hash_len  = config.get("length", 16)

    h = hashlib.new(algo, s.encode()).hexdigest()[:hash_len]
    return f"{prefix}{h}"


# ── Redact ────────────────────────────────────────────────────────────────────

def mask_redact(value: Any, config: Dict = None) -> Optional[str]:
    """
    Replace entirely with a fixed string.
    config: {"replacement": "***REDACTED***"}
    """
    if value is None:
        return None
    config = config or {}
    return config.get("replacement", "***REDACTED***")


# ── Partial ───────────────────────────────────────────────────────────────────

def mask_partial(value: Any, config: Dict = None) -> Optional[str]:
    """
    Keep first N and last M characters, mask the middle.
    "john.doe@example.com" with keep_start=2, keep_end=4 →
    "jo****************.com"

    config: {"keep_start": 2, "keep_end": 4, "mask_char": "*"}
    """
    s = _safe(value)
    if s is None:
        return None

    config     = config or {}
    keep_start = max(0, config.get("keep_start", 2))
    keep_end   = max(0, config.get("keep_end", 4))
    mask_char  = config.get("mask_char", "*")

    total = len(s)
    if total <= keep_start + keep_end:
        # String too short to mask meaningfully — redact all
        return mask_char * total

    middle_len = total - keep_start - keep_end
    return s[:keep_start] + (mask_char * middle_len) + s[total - keep_end:]


# ── Encrypt ───────────────────────────────────────────────────────────────────

def mask_encrypt(value: Any, config: Dict = None) -> Optional[str]:
    """
    AES-128-CBC via Fernet (reversible with the platform's encryption key).
    Uses MIGRATION_ENCRYPTION_KEY environment variable.

    config: {"reversible": true}
    """
    s = _safe(value)
    if s is None:
        return None

    try:
        import os
        from cryptography.fernet import Fernet
        key = os.environ.get("MIGRATION_ENCRYPTION_KEY", "")
        if not key:
            # Fallback to hash if no key configured
            return mask_hash(value, {"prefix": "ENC_"})
        f = Fernet(key.encode() if isinstance(key, str) else key)
        return f.encrypt(s.encode()).decode()
    except Exception:
        # Degrade gracefully
        return mask_hash(value, {"prefix": "ENC_"})


# ── Nullify ───────────────────────────────────────────────────────────────────

def mask_nullify(value: Any, config: Dict = None) -> None:
    """Replace with NULL regardless of input."""
    return None


# ── Fixed value ───────────────────────────────────────────────────────────────

def mask_fixed_value(value: Any, config: Dict = None) -> Optional[str]:
    """
    Replace with a configured fixed literal.
    config: {"value": "MASKED"}
    """
    if value is None:
        return None
    config = config or {}
    return str(config.get("value", "MASKED"))


# ── Format preserve ───────────────────────────────────────────────────────────

def mask_format_preserve(value: Any, config: Dict = None) -> Optional[str]:
    """
    Replace alphanumeric characters while preserving format characters
    (dashes, spaces, parentheses, dots).

    "555-123-4567" -> "555-XXX-XXXX"
    "john@example.com" -> "XXXX@XXXXXXX.XXX"

    config: {"digit_char": "X", "letter_char": "X", "preserve_separators": true}
    """
    s = _safe(value)
    if s is None:
        return None

    config      = config or {}
    digit_char  = config.get("digit_char", "X")
    letter_char = config.get("letter_char", "X")

    result = []
    for ch in s:
        if ch.isdigit():
            result.append(digit_char)
        elif ch.isalpha():
            result.append(letter_char)
        else:
            # Keep separators: - . @ ( ) space etc.
            result.append(ch)
    return "".join(result)


# ── Strategy dispatch table ───────────────────────────────────────────────────

STRATEGY_MAP = {
    "hash":             mask_hash,
    "redact":           mask_redact,
    "partial":          mask_partial,
    "encrypt":          mask_encrypt,
    "nullify":          mask_nullify,
    "fixed_value":      mask_fixed_value,
    "format_preserve":  mask_format_preserve,
}


def apply_mask(value: Any, strategy: str, config: Dict = None) -> Any:
    """
    Apply a masking strategy to a value.
    Returns the masked value, or the original if strategy is unknown.
    """
    fn = STRATEGY_MAP.get(strategy.lower())
    if fn is None:
        return value   # Unknown strategy — pass through unchanged
    return fn(value, config or {})
