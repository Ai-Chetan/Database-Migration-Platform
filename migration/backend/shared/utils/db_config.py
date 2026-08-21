"""
Database Config Normalizer
File: migration/backend/shared/utils/db_config.py

ROOT CAUSE THIS FIXES:
    POST /jobs and POST /jobs/{id}/planning/compute both accept a raw
    source_config / target_config dict straight from the client (see
    control_plane/app/routers/jobs.py, control_plane/app/routers/planning.py).
    That dict is then read by ~15 different files across the codebase
    (enterprise/adaptive_chunk_planner/planner.py, worker_service readers
    and writers, schema_mapping_service validator, rollback_engine,
    resource_governor, checksum_validator, connector_framework connectors)
    - every single one of them reads config.get("user").

    But the documented/example payload (and what test_e2e_api.py actually
    sends, matching what the frontend would have to send today) uses the
    key "username", not "user":

        {"engine": "mysql", "host": ..., "database": ...,
         "username": "root", "password": "..."}

    So config.get("user") silently returned None. Passing user=None into
    mysql.connector.connect() doesn't raise - the underlying client falls
    back to an OS/driver default (on Windows this surfaces as the bizarre
    "Access denied for user 'ODBC'@'localhost'" error seen in the logs),
    which is a nightmare to debug because the error looks unrelated to the
    real cause.

    Fixing all 15 read sites individually is possible but riskier (more
    files touched, more chance of missing one, and any future new
    connector would reintroduce the bug). Instead, this normalizer runs
    ONCE at the boundary where external input becomes a persisted
    source_config/target_config: job creation (job_manager.create_job) and
    the standalone chunk-planning endpoint (planning.py compute endpoint).
    Every downstream reader keeps working unmodified because the dict it
    receives always has the canonical keys by the time it gets there.

Canonical shape after normalization:
    {
        "engine":   str   ("mysql" | "postgresql" | ...),
        "host":     str,
        "port":     int,
        "database": str,
        "user":     str,
        "password": str,
        ... any other keys passed through unchanged ...
    }
"""

from typing import Any, Dict


# Accepted synonyms -> canonical key. Order matters: first match wins.
_SYNONYMS = {
    "user":     ["user", "username", "db_user", "user_name"],
    "password": ["password", "db_password", "passwd", "pwd"],
    "database": ["database", "database_name", "db", "db_name", "dbname"],
    "host":     ["host", "hostname", "db_host"],
    "port":     ["port", "db_port"],
    "engine":   ["engine", "db_type", "database_type", "dialect"],
}


def normalize_db_config(config: Dict[str, Any]) -> Dict[str, Any]:
    """
    Returns a NEW dict with canonical keys (user/password/database/host/
    port/engine) populated from whichever synonym the caller used, plus any
    other keys from the input passed through unchanged. Does not mutate the
    input dict. Safe to call multiple times (idempotent).

    Raises ValueError if, after checking all synonyms, a required key
    (user, database, host) still cannot be resolved - this turns a silent
    "connects as the wrong/blank user" failure into an immediate, clear
    400 at job-creation time instead of a confusing error deep in a
    background worker minutes later.
    """
    if not isinstance(config, dict):
        raise ValueError("Database connection config must be an object.")

    result: Dict[str, Any] = dict(config)  # pass through unknown keys as-is

    for canonical, synonyms in _SYNONYMS.items():
        if canonical in result and result[canonical] not in (None, ""):
            continue  # already canonical and non-empty
        for syn in synonyms:
            if syn in config and config[syn] not in (None, ""):
                result[canonical] = config[syn]
                break

    if "port" in result and result["port"] is not None:
        try:
            result["port"] = int(result["port"])
        except (TypeError, ValueError):
            raise ValueError(f"Invalid port value: {result['port']!r}")

    if "engine" in result and isinstance(result["engine"], str):
        result["engine"] = result["engine"].strip().lower()

    missing = [k for k in ("host", "database", "user") if not result.get(k)]
    if missing:
        raise ValueError(
            "Database connection config is missing required field(s): "
            + ", ".join(missing)
            + ". Accepted key names: user/username, database/database_name, "
              "host, port, password, engine/db_type."
        )

    return result
