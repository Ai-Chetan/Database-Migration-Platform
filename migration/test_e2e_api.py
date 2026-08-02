"""
End-to-End HTTP API Test
File: migration/test_e2e_api.py

Tests the platform through its REAL HTTP API - the exact same endpoints
the frontend calls - rather than importing Python classes directly. This
is deliberately different from test_worker_e2e.py (which bypasses the API
and calls Planner/ChunkExecutor directly): the point of THIS script is to
catch integration bugs between the API layer and the frontend contract,
not just verify the underlying orchestration logic works in isolation.

What this does, in order:
    1. Registers a new tenant + tenant_admin user (easy demo credentials)
    2. Invites + accepts one demo user per remaining role (easy passwords)
    3. Logs in as the admin
    4. Registers a source connection (billnbox) and target connection
       (billnboxtest) - both MySQL on localhost:3306
    5. Tests both connections live
    6. Pre-creates the target `owner` table (the platform does not
       auto-generate target DDL as part of the execution path - table
       structure has to exist on the target before chunks can write to it;
       DDL generation is a separate, manual Schema Mapping Service step)
    7. Creates a migration job, registers the `owner` table, computes the
       chunk plan (this also generates the actual migration_chunks rows
       and pushes them to Redis - see the Stage 1 fix note below)
    8. Starts the job
    9. Prints the exact command to start a worker in a separate terminal
   10. Polls job status until complete (or a timeout), printing progress
   11. Queries both databases directly and compares row counts

BEFORE RUNNING:
    pip install requests mysql-connector-python
    Edit the CONFIG block below with your real MySQL credentials.
    Make sure the backend is running: uvicorn backend.main:app --reload
    Make sure Redis and the metadata Postgres DB are running.
    Make sure the `billnbox` database and `owner` table already exist
    with some rows in it (this script does not create source data).

STAGE 1 CONTEXT (why this script exists / what it will catch):
    Several real bugs were found and fixed in the backend this session,
    including one that would have made this exact test hang forever with
    zero visible error: AdaptiveChunkPlanner.compute_all_tables() computed
    chunk SIZING metadata, but nothing anywhere in the codebase actually
    called Planner.generate_chunks() to create the real migration_chunks
    rows and push them to the Redis queue - so a job could reach "started"
    with literally no work items for any worker to ever pull. That's now
    wired into POST /jobs/{id}/planning/compute. If this script hangs at
    the polling step with total_chunks staying at 0, that fix didn't make
    it into the code you're running against.

    Also fixed: migration_jobs.status never transitioned to 'running'
    anywhere (jumped straight from 'planning' to 'completed'/'failed').
    If start_job() output below shows status stuck at "planning" instead
    of "running", that fix isn't present either.

    Job creation currently only accepts raw source_config/target_config
    credential dicts (POST /jobs), not connection_id references - that
    integration gap is still open as of this script (see the running
    conversation for status). This script works around it by reading the
    connection details back out and re-embedding the plaintext credentials
    directly into the job payload, exactly like the current frontend would
    have to (badly) if it tried to do this today.
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
MYSQL_PASSWORD = "root"       # <-- edit
SOURCE_DB = "billnbox"
TARGET_DB = "billnboxtest"

TENANT_NAME = "Demo Co"
TENANT_SLUG = "demo-co"
ADMIN_EMAIL = "admin@demo.local"
ADMIN_PASSWORD = "Demo@1234"
ADMIN_NAME = "Demo Admin"

# One demo account per remaining role (tenant_admin is the one created above).
# All use the same easy password so you can log into the frontend and click
# around as each role without hunting for credentials.
DEMO_PASSWORD = "Demo@1234"
DEMO_ROLES = [
    "migration_admin",
    "migration_operator",
    "read_only",
    "auditor",
    "api_client",
    "platform_admin",  # unusual to invite within a tenant, included for completeness
]

TABLE_NAME = "owner"
PRIMARY_KEY_COLUMN = "OwnerID"

# Exact structure you gave for `owner`, used to pre-create it on the target.
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
    """Thin wrapper that prints the real request/response so failures are
    immediately diagnosable, matching how you'd read a browser network tab."""
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


def main():
    admin_token = None
    source_conn_id = None
    target_conn_id = None
    job_id = None

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

    # ── Step 2: Invite + accept demo users for every other role ───────────
    step(2, "Create demo accounts for every role")
    for role in DEMO_ROLES:
        email = f"{role}@demo.local"
        resp = request("POST", "/auth/invite", token=admin_token, json={"email": email, "role": role})
        if resp.status_code != 200:
            fail(f"Could not invite {role} ({email}) - skipping")
            continue
        invite_token = resp.json()["token"]

        resp2 = request("POST", "/auth/invite/accept", json={
            "token": invite_token,
            "full_name": f"Demo {role.replace('_', ' ').title()}",
            "password": DEMO_PASSWORD,
        })
        if resp2.status_code == 200:
            ok(f"{role:<20} -> {email} / {DEMO_PASSWORD}")
        else:
            fail(f"Invite created for {role} but accept failed: {resp2.status_code}")

    info("\n  All demo accounts (password for all: " + DEMO_PASSWORD + "):")
    info(f"    tenant_admin         -> {ADMIN_EMAIL}")
    for role in DEMO_ROLES:
        info(f"    {role:<20} -> {role}@demo.local")

    # ── Step 3: Register connections ────────────────────────────────────────
    step(3, "Register source + target connections")
    resp = request("POST", "/connections", token=admin_token, json={
        "name": "billnbox (source)",
        "db_type": "mysql",
        "host": MYSQL_HOST,
        "port": MYSQL_PORT,
        "database_name": SOURCE_DB,
        "username": MYSQL_USER,
        "password": MYSQL_PASSWORD,
        "test_before_save": True,
    })
    if resp.status_code != 200:
        fail("Could not create source connection. Check MySQL credentials in the CONFIG block.")
        return
    source_conn_id = resp.json()["id"]
    ok(f"Source connection created: {source_conn_id}")

    resp = request("POST", "/connections", token=admin_token, json={
        "name": "billnboxtest (target)",
        "db_type": "mysql",
        "host": MYSQL_HOST,
        "port": MYSQL_PORT,
        "database_name": TARGET_DB,
        "username": MYSQL_USER,
        "password": MYSQL_PASSWORD,
        "test_before_save": True,
    })
    if resp.status_code != 200:
        fail(f"Could not create target connection. Does the '{TARGET_DB}' database exist yet?")
        return
    target_conn_id = resp.json()["id"]
    ok(f"Target connection created: {target_conn_id}")

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

    # Also grab the source row count directly for the final comparison.
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

    # ── Step 6: Create the job ──────────────────────────────────────────────
    step(6, "Create migration job")
    # NOTE: /jobs currently only accepts raw source_config/target_config -
    # it does not (yet) accept connection_id references. Re-embedding the
    # plaintext credentials here mirrors exactly what the frontend would
    # have to do today given that gap (see script header).
    source_config = {
        "engine": "mysql", "host": MYSQL_HOST, "port": MYSQL_PORT,
        "database": SOURCE_DB, "username": MYSQL_USER, "password": MYSQL_PASSWORD,
    }
    target_config = {
        "engine": "mysql", "host": MYSQL_HOST, "port": MYSQL_PORT,
        "database": TARGET_DB, "username": MYSQL_USER, "password": MYSQL_PASSWORD,
    }
    resp = request("POST", "/jobs", token=admin_token, json={
        "source_config": source_config,
        "target_config": target_config,
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
        "source_config": source_config,
        "source_db_type": "mysql",
        "target_db_type": "mysql",
        "primary_key_columns": {TABLE_NAME: PRIMARY_KEY_COLUMN},
    })
    if resp.status_code != 200:
        fail("Chunk planning failed")
        return
    plan = resp.json()
    total_chunks = plan["total_chunks"]
    if total_chunks == 0:
        fail("total_chunks came back as 0 - if the source table has rows, this "
             "means Planner.generate_chunks() isn't wired into /planning/compute "
             "in the code you're running. Nothing will happen if you start this job.")
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
        fail(f"Job status is '{status}', expected 'running' - the status-transition "
             "fix may not be present in the code you're running.")

    # ── Step 9: Prompt to start a worker ─────────────────────────────────────
    step(9, "Start a worker")
    info("This script does not start a worker for you (worker env/PYTHONPATH")
    info("setup varies too much to do reliably from here). In a SEPARATE")
    info("terminal, from the migration/ directory, run:\n")
    print(f"    WORKER_ID=worker-1 TENANT_ID={TENANT_SLUG} python -m backend.worker_service.app.worker\n")
    input("  Press Enter once the worker is running and you see 'Worker ready'...")

    # ── Step 10: Poll until complete ─────────────────────────────────────────
    step(10, f"Polling job status (every {POLL_INTERVAL_SEC}s, up to {POLL_TIMEOUT_SEC}s)")
    started = time.time()
    last_progress = None
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

    if job["status"] == "completed":
        ok("Job completed")
    elif job["status"] == "failed":
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
