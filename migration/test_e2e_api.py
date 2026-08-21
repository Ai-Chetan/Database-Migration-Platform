"""
End-to-End HTTP API Test
File: migration/test_e2e_api.py

Tests the platform through its REAL HTTP API - the exact same endpoints
the frontend calls - rather than importing Python classes directly.

CHANGES IN THIS VERSION:
  - STEP 2 no longer uses the invitation flow (POST /auth/invite and
    /auth/invite/accept were both REMOVED per product decision - admins
    now create users directly). Rewritten to call
    POST /tenants/{tenant_id}/users, matching enterprise/routers/tenants.py.
  - Every step that creates a named resource (tenant, connections, demo
    users) is now idempotent: if it already exists from a previous run,
    the script looks it up and reuses it instead of failing. Previously
    only tenant/admin registration handled this - connection creation did
    not, so re-running this script against a database that already had
    connections from a prior run would immediately 500 and abort with
    nothing else tested.
  - STEP 6 (create job) now uses source_connection_id / target_connection_id
    instead of re-embedding plaintext credentials into the job payload.
    POST /jobs previously only accepted raw source_config/target_config
    dicts - which meant the ONLY way to create a job was to already have
    the plaintext password sitting in your test script. It now accepts a
    connection_id pair directly and resolves credentials server-side
    (see control_plane/app/routers/jobs.py) - this is also what the New
    Migration wizard's frontend does now.
  - Added STEP 2b: a quick forgot-password / reset-password smoke test,
    since those endpoints didn't exist at all before this round of fixes.

BEFORE RUNNING:
    pip install requests mysql-connector-python
    Edit the CONFIG block below with your real MySQL credentials.
    Make sure the backend is running: uvicorn backend.main:app --reload
    Make sure Redis and the metadata Postgres DB are running, and that
    db_migrations/019 and 020 have been applied.
    Make sure the `billnbox` database and `owner` table already exist
    with some rows in it (this script does not create source data).
"""

import sys
import time
import datetime

import requests

try:
    import mysql.connector
except ImportError:
    print("Missing dependency. Run: pip install mysql-connector-python")
    sys.exit(1)


# ─── CONFIG - EDIT THIS BLOCK ──────────────────────────────────────────────

BASE_URL = "http://localhost:8000"

MYSQL_HOST = "localhost"
MYSQL_PORT = 3306
MYSQL_USER = "root"           # <-- edit
MYSQL_PASSWORD = " "       # <-- edit
SOURCE_DB = "billnbox"
TARGET_DB = "billnboxtest"

TENANT_NAME = "Demo Co"
TENANT_SLUG = "demo-co"
ADMIN_EMAIL = "admin@demo.local"
ADMIN_PASSWORD = "Demo@1234"
ADMIN_NAME = "Demo Admin"

# One demo account per remaining role (tenant_admin is the one created above).
DEMO_PASSWORD = "Demo@1234"
DEMO_ROLES = [
    "migration_admin",
    "migration_operator",
    "read_only",
    "auditor",
    "api_client",
    "platform_admin",  # unusual to create within a tenant, included for completeness
]

TABLE_NAME = "owner"
PRIMARY_KEY_COLUMN = "OwnerID"

TARGET_TABLE_DDL = f"""
CREATE TABLE IF NOT EXISTS `{TABLE_NAME}` (
    `OwnerID` INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    `Username` VARCHAR(50),
    `Password` VARCHAR(50),
    `Name` VARCHAR(50),
    `EmailID` VARCHAR(50),
    `PhoneNo` VARCHAR(10),
    `ShopName` VARCHAR(50),
    `ShopAddress` VARCHAR(100),
    `FilePath` VARCHAR(225)
)
"""

POLL_INTERVAL_SEC = 3
POLL_TIMEOUT_SEC = 300  # 5 minutes

# ────────────────────────────────────────────────────────────────────────────


def step(n, title):
    print(f"\n{'='*70}\nSTEP {n}: {title}\n{'='*70}")


def ok(msg):
    print(f"  [OK] {msg}")


def fail(msg):
    print(f"  [FAIL] {msg}")


def info(msg):
    print(f"  {msg}")


def request(method, path, token=None, **kwargs):
    url = f"{BASE_URL}{path}"
    headers = kwargs.pop("headers", {})
    if token:
        headers["Authorization"] = f"Bearer {token}"
    resp = requests.request(method, url, headers=headers, timeout=15, **kwargs)
    if resp.status_code >= 400:
        info(f"{method} {path} -> {resp.status_code}")
        try:
            info(f"  response: {resp.json()}")
        except Exception:
            info(f"  response: {resp.text[:300]}")
    return resp


def get_or_create_connection(token, name, db_name):
    """Idempotent: reuses an existing connection with this name if one
    already exists (from a prior run of this script) instead of 500ing on
    the unique (tenant_id, name) constraint."""
    resp = request("POST", "/connections", token=token, json={
        "name": name,
        "engine": "mysql",
        "host": MYSQL_HOST,
        "port": MYSQL_PORT,
        "database": db_name,
        "username": MYSQL_USER,
        "password": MYSQL_PASSWORD,
        "test_before_save": True,
    })
    if resp.status_code == 200:
        return resp.json()["id"]
    if resp.status_code == 400 and "already exists" in resp.text.lower():
        info(f"Connection '{name}' already exists - reusing it")
        listing = request("GET", "/connections", token=token)
        if listing.status_code == 200:
            for conn in listing.json():
                if conn["name"] == name:
                    return conn["id"]
        fail(f"Connection '{name}' reportedly exists but wasn't found in the list")
        return None
    fail(f"Could not create or find connection '{name}'")
    return None


def main():
    admin_token = None
    tenant_id = None
    source_conn_id = None
    target_conn_id = None
    job_id = None
    source_row_count = 0

    # ── Step 1: Register admin + tenant ────────────────────────────────────
    step(1, "Register tenant + admin user")
    resp = request("POST", "/auth/register", json={
        "tenant_name": TENANT_NAME,
        "tenant_slug": TENANT_SLUG,
        "full_name": ADMIN_NAME,
        "email": ADMIN_EMAIL,
        "password": ADMIN_PASSWORD,
        "plan_name": "free",
    })
    if resp.status_code == 200:
        admin_token = resp.json()["token"]
        ok(f"Registered tenant '{TENANT_NAME}' + admin {ADMIN_EMAIL}")
    elif resp.status_code == 400 and "already" in resp.text.lower():
        info("Tenant/email already exists - logging in instead")
        resp = request("POST", "/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
        if resp.status_code != 200:
            fail("Could not register OR log in. Check the backend is running and the DB is reachable.")
            return
        admin_token = resp.json()["token"]
        ok("Logged in as existing admin")
    else:
        fail(f"Registration failed unexpectedly: {resp.status_code}")
        return

    me = request("GET", "/auth/me", token=admin_token)
    if me.status_code != 200:
        fail("GET /auth/me failed - can't determine tenant_id for subsequent steps. "
             "If this 500s with 'column phone does not exist', run "
             "db_migrations/019_fix_user_roles_and_admin_management.sql first.")
        return
    tenant_id = me.json()["tenant_id"]
    info(f"tenant_id = {tenant_id}")

    # ── Step 2: Create demo accounts for every role, directly ──────────────
    step(2, "Create demo accounts for every role (direct create, not invite)")
    for role in DEMO_ROLES:
        email = f"{role}@demo.local"
        resp = request("POST", f"/tenants/{tenant_id}/users", token=admin_token, json={
            "email": email,
            "full_name": f"Demo {role.replace('_', ' ').title()}",
            "role": role,
            "password": DEMO_PASSWORD,
            "must_change_password": False,
            "send_welcome_email": False,
        })
        if resp.status_code == 200:
            ok(f"{role:<20} -> {email} / {DEMO_PASSWORD}")
        elif resp.status_code == 400 and "already" in resp.text.lower():
            info(f"{role:<20} -> {email} already exists, skipping")
        else:
            fail(f"Could not create {role} ({email})")

    info("\n  All demo accounts (password for all: " + DEMO_PASSWORD + "):")
    info(f"    tenant_admin         -> {ADMIN_EMAIL}")
    for role in DEMO_ROLES:
        info(f"    {role:<20} -> {role}@demo.local")

    # ── Step 2b: Forgot-password / reset-password smoke test ───────────────
    step("2b", "Forgot-password / reset-password smoke test")
    resp = request("POST", "/auth/forgot-password", json={"email": ADMIN_EMAIL})
    if resp.status_code == 200:
        ok("POST /auth/forgot-password returned 200 (check server logs or your inbox "
           "for the reset link if SMTP is configured - the token itself is never "
           "returned in the API response, by design)")
    else:
        fail("POST /auth/forgot-password did not return 200")

    # ── Step 3: Register source + target connections (idempotent) ──────────
    step(3, "Register source + target connections")
    source_conn_id = get_or_create_connection(admin_token, "billnbox (source)", SOURCE_DB)
    if source_conn_id:
        ok(f"Source connection: {source_conn_id}")
    target_conn_id = get_or_create_connection(admin_token, "billnboxtest (target)", TARGET_DB)
    if target_conn_id:
        ok(f"Target connection: {target_conn_id}")
    if not source_conn_id or not target_conn_id:
        fail("Could not obtain both connections - stopping.")
        return

    # ── Step 4: Test both connections live ─────────────────────────────────
    step(4, "Test both connections")
    for label, cid in [("source", source_conn_id), ("target", target_conn_id)]:
        resp = request("POST", f"/connections/{cid}/test", token=admin_token)
        result = resp.json()
        if result.get("success"):
            ok(f"{label}: reachable, {result.get('latency_ms')}ms, {result.get('db_version', '')}")
        else:
            fail(f"{label}: {result.get('error')}")
            return

    # ── Step 5: Pre-create target table ─────────────────────────────────────
    step(5, f"Pre-create target table `{TABLE_NAME}` in {TARGET_DB}")
    info("The platform does not auto-generate target DDL as part of job "
         "execution - the table must already exist on the target.")
    try:
        conn = mysql.connector.connect(
            host=MYSQL_HOST, port=MYSQL_PORT, user=MYSQL_USER,
            password=MYSQL_PASSWORD, database=TARGET_DB,
        )
        cur = conn.cursor()
        cur.execute(TARGET_TABLE_DDL)
        conn.commit()
        cur.execute(f"SELECT COUNT(*) FROM `{TABLE_NAME}`")
        existing = cur.fetchone()[0]
        cur.close()
        conn.close()
        ok(f"Target table ready ({existing} existing rows)")
    except Exception as e:
        fail(f"Could not create target table: {e}")
        return

    try:
        conn = mysql.connector.connect(
            host=MYSQL_HOST, port=MYSQL_PORT, user=MYSQL_USER,
            password=MYSQL_PASSWORD, database=SOURCE_DB,
        )
        cur = conn.cursor()
        cur.execute(f"SELECT COUNT(*) FROM `{TABLE_NAME}`")
        source_row_count = cur.fetchone()[0]
        cur.close()
        conn.close()
        info(f"Source table has {source_row_count} rows to migrate")
        if source_row_count == 0:
            fail(f"`{SOURCE_DB}.{TABLE_NAME}` has zero rows - add test data first, "
                 "or the migration will trivially 'succeed' with nothing to verify.")
    except Exception as e:
        fail(f"Could not read source table: {e}")
        return

    # ── Step 6: Create the job (connection-ID based, no plaintext creds) ────
    step(6, "Create migration job")
    resp = request("POST", "/jobs", token=admin_token, json={
        "source_connection_id": source_conn_id,
        "target_connection_id": target_conn_id,
    })
    if resp.status_code != 200:
        fail("Job creation failed")
        return
    job_id = resp.json()["id"]
    ok(f"Job created: {job_id}")

    # ── Step 7: Register table + compute chunk plan ─────────────────────────
    step(7, "Register table + compute chunk plan")
    resp = request("POST", f"/jobs/{job_id}/planning/tables", token=admin_token, json={
        "tables": [TABLE_NAME],
        "primary_key_columns": {TABLE_NAME: PRIMARY_KEY_COLUMN},
    })
    if resp.status_code != 200:
        fail("Could not register table")
        return
    ok(f"Table `{TABLE_NAME}` registered")

    resp = request("POST", f"/jobs/{job_id}/planning/compute", token=admin_token, json={
        "source_config": {
            "engine": "mysql", "host": MYSQL_HOST, "port": MYSQL_PORT,
            "database": SOURCE_DB, "username": MYSQL_USER, "password": MYSQL_PASSWORD,
        },
        "source_db_type": "mysql",
        "target_db_type": "mysql",
        "primary_key_columns": {TABLE_NAME: PRIMARY_KEY_COLUMN},
    })
    if resp.status_code != 200:
        fail("Chunk planning failed. If this 500s with a NotNullViolation on "
             "migration_chunks.table_name, make sure you're running the latest "
             "control_plane/app/orchestrator/planner.py.")
        return
    plan = resp.json()
    total_chunks = plan["total_chunks"]
    if total_chunks == 0:
        fail("total_chunks came back as 0 - nothing will happen if you start this job.")
    else:
        ok(f"Chunk plan computed: {total_chunks} chunk(s) created and queued")

    # ── Step 8: Start the job ───────────────────────────────────────────────
    step(8, "Start the job")
    resp = request("POST", f"/jobs/{job_id}/start", token=admin_token)
    if resp.status_code != 200:
        fail("Could not start job")
        return
    status = resp.json()["status"]
    if status == "running":
        ok("Job status is 'running'")
    else:
        fail(f"Job status is '{status}', expected 'running'")

    # ── Step 9: Prompt to start a worker ─────────────────────────────────────
    step(9, "Start a worker")
    info("This script does not start a worker for you. In a SEPARATE")
    info("terminal, from the migration/ directory, run:\n")
    print(f"    WORKER_ID=worker-1 TENANT_ID={TENANT_SLUG} python -m backend.worker_service.app.worker\n")
    input("  Press Enter once the worker is running and you see 'Worker ready'...")

    # ── Step 10: Poll until complete ─────────────────────────────────────────
    step(10, f"Polling job status (every {POLL_INTERVAL_SEC}s, up to {POLL_TIMEOUT_SEC}s)")
    started = time.time()
    last_progress = None
    job = None
    while time.time() - started < POLL_TIMEOUT_SEC:
        resp = request("GET", f"/jobs/{job_id}", token=admin_token)
        job = resp.json()
        progress = f"{job['completed_chunks']}/{job['total_chunks']} chunks, status={job['status']}"
        if progress != last_progress:
            info(progress)
            last_progress = progress
        if job["status"] in ("completed", "failed"):
            break
        time.sleep(POLL_INTERVAL_SEC)
    else:
        fail(f"Timed out after {POLL_TIMEOUT_SEC}s waiting for the job to finish. "
             "Check the worker's terminal output for errors.")

    if job and job["status"] == "completed":
        ok("Job completed")
    elif job and job["status"] == "failed":
        fail(f"Job failed: {job.get('last_error')}")

    # ── Step 11: Verify row counts ────────────────────────────────────────────
    step(11, "Verify migrated data")
    try:
        conn = mysql.connector.connect(
            host=MYSQL_HOST, port=MYSQL_PORT, user=MYSQL_USER,
            password=MYSQL_PASSWORD, database=TARGET_DB,
        )
        cur = conn.cursor()
        cur.execute(f"SELECT COUNT(*) FROM `{TABLE_NAME}`")
        target_row_count = cur.fetchone()[0]
        cur.execute(f"SELECT * FROM `{TABLE_NAME}` LIMIT 3")
        sample = cur.fetchall()
        cur.close()
        conn.close()

        info(f"Source rows: {source_row_count}")
        info(f"Target rows: {target_row_count}")
        if target_row_count == source_row_count:
            ok("Row counts match")
        else:
            fail(f"Row count mismatch ({source_row_count} source vs {target_row_count} target)")

        info("Sample migrated rows:")
        for row in sample:
            info(f"  {row}")
    except Exception as e:
        fail(f"Could not verify target data: {e}")

    print(f"\n{'='*70}")
    print(f"Done. Job ID: {job_id}")
    print(f"Check it in the frontend at /app/jobs/{job_id}")
    print(f"{'='*70}\n")


if __name__ == "__main__":
    main()
