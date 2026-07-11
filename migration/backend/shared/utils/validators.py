import uuid
import re

def validate_uuid(val: str) -> bool:
    try:
        uuid_obj = uuid.UUID(val, version=4)
        return str(uuid_obj) == val
    except ValueError:
        return False

def validate_table_name(name: str) -> bool:
    # Allow alphanumeric and underscores, must start with letter
    pattern = r"^[a-zA-Z][a-zA-Z0-9_]*$"
    return bool(re.match(pattern, name))

def validate_column_name(name: str) -> bool:
    return validate_table_name(name)

def validate_mapping(mapping: dict) -> bool:
    if not isinstance(mapping, dict):
        return False
    if "source" not in mapping or "target" not in mapping:
        return False
    return True
