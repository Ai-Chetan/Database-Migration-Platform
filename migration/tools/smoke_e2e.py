#!/usr/bin/env python3
r"""
smoke_e2e.py - one-command regression check of the whole migration path.

WHERE:  E:\Project\Migration\migration\tools\smoke_e2e.py
RUN:    (API + worker + Redis + Postgres + MySQL must be running)
            python tools\smoke_e2e.py                 # positive scenario
            python tools\smoke_e2e.py --scenario negative
            python tools\smoke_e2e.py --scenario all
Exit code 0 = all passed, 1 = failure (a message says which step).

Scenarios
  positive : 2,500 rows, primary keys 10003..17500 with gaps (catches the old "1..N chunk range"
             bug) -> job must complete and target must equal source (count, min, max, checksum).
  negative : a value too long for the target column -> the job must FAIL, the error must say so
             and the target must stay empty (catches silent truncation / INSERT IGNORE).
             NOTE: after session S11 introduces reject-and-continue policies this scenario will be
             updated; today the default policy is fail-fast.

Config (environment variables, defaults in brackets)
  API_URL [http://localhost:8000]   ADMIN_EMAIL [admin@demo.local]   ADMIN_PASSWORD [Demo@1234]
  TENANT_SLUG [demo-co]
  MYSQL_HOST [127.0.0.1] MYSQL_PORT [3306] MYSQL_USER [root] MYSQL_PASSWORD []
  SRC_DB [billnbox]  TGT_DB [billnboxtest]     (both databases must already exist)
  SMOKE_TIMEOUT [180] seconds to wait for a job
The script creates/overwrites ONLY the tables smoke_owner and smoke_trunc in those two databases.
"""
import argparse, os, sys, time, json
import requests
import pymysql

API = os.environ.get("API_URL", "http://localhost:8000").rstrip("/")
EMAIL = os.environ.get("ADMIN_EMAIL", "admin@demo.local")
PASSWORD = os.environ.get("ADMIN_PASSWORD", "Demo@1234")
SLUG = os.environ.get("TENANT_SLUG", "demo-co")
MY = dict(host=os.environ.get("MYSQL_HOST", "127.0.0.1"), port=int(os.environ.get("MYSQL_PORT", "3306")),
          user=os.environ.get("MYSQL_USER", "root"), password=os.environ.get("MYSQL_PASSWORD", ""))
SRC_DB = os.environ.get("SRC_DB", "billnbox")
TGT_DB = os.environ.get("TGT_DB", "billnboxtest")
TIMEOUT = int(os.environ.get("SMOKE_TIMEOUT", "180"))


class Fail(Exception):
    pass


def step(msg):
    print(f"  - {msg}", flush=True)


def my(db):
    return pymysql.connect(database=db, autocommit=True, charset="utf8mb4", **MY)


def api(method, path, token=None, **kw):
    h = {"Authorization": f"Bearer {token}"} if token else {}
    r = requests.request(method, API + path, headers=h, timeout=60, **kw)
    return r


def login():
    r = api("POST", "/auth/login", json={"email": EMAIL, "password": PASSWORD})
    if r.status_code != 200:
        r = api("POST", "/auth/register", json={"tenant_name": "Demo Co", "tenant_slug": SLUG,
                                                "email": EMAIL, "password": PASSWORD, "full_name": "Demo Admin"})
        r = api("POST", "/auth/login", json={"email": EMAIL, "password": PASSWORD})
    if r.status_code != 200:
        raise Fail(f"login failed: HTTP {r.status_code} {r.text[:200]}")
    body = r.json()
    tok = body.get("token") or body.get("access_token")
    if not tok:
        raise Fail(f"login response has no token: {list(body)}")
    return tok


def get_connection(tok, name, db):
    r = api("GET", "/connections", tok)
    if r.status_code == 200:
        for c in r.json():
            if c.get("name") == name and c.get("database_name") == db:
                return c["id"]
    body = {"name": name, "db_type": "mysql", "host": MY["host"], "port": MY["port"], "database_name": db,
            "username": MY["user"], "password": MY["password"], "test_before_save": True}
    r = api("POST", "/connections", tok, json=body)
    if r.status_code != 200:
        raise Fail(f"POST /connections failed: HTTP {r.status_code} {r.text[:300]}")
    return r.json()["id"]


def run_job(tok, src_id, tgt_id, table, pk):
    r = api("POST", "/jobs", tok, json={"source_connection_id": src_id, "target_connection_id": tgt_id})
    if r.status_code != 200:
        raise Fail(f"create job: HTTP {r.status_code} {r.text[:300]}")
    job = r.json()
    jid = job["id"]
    if "password" in json.dumps(job.get("source_config") or {}) and '"password": "***"' not in json.dumps(job.get("source_config") or {}):
        raise Fail("SECURITY: job response exposes a database password")
    r = api("POST", f"/jobs/{jid}/planning/tables", tok, json={"tables": [table], "primary_key_columns": {table: pk}})
    if r.status_code != 200:
        raise Fail(f"register tables: HTTP {r.status_code} {r.text[:300]}")
    cfg = {"engine": "mysql", "host": MY["host"], "port": MY["port"], "database": SRC_DB,
           "username": MY["user"], "password": MY["password"]}
    r = api("POST", f"/jobs/{jid}/planning/compute", tok,
            json={"source_config": cfg, "source_db_type": "mysql", "target_db_type": "mysql",
                  "primary_key_columns": {table: pk}})
    if r.status_code != 200:
        raise Fail(f"compute plan: HTTP {r.status_code} {r.text[:300]}")
    n = r.json().get("total_chunks")
    step(f"planned {n} chunk(s)")
    if not n:
        raise Fail("no chunks were planned (the old 'estimated row count = 0' bug)")
    r = api("POST", f"/jobs/{jid}/start", tok)
    if r.status_code != 200:
        raise Fail(f"start job: HTTP {r.status_code} {r.text[:300]}")
    deadline = time.time() + TIMEOUT
    last = None
    while time.time() < deadline:
        j = api("GET", f"/jobs/{jid}", tok).json()
        last = j
        if j["status"] in ("completed", "failed", "cancelled"):
            return j
        time.sleep(2)
    raise Fail(f"timeout after {TIMEOUT}s, last status={last and last.get('status')} "
               f"(is a worker running?  python -m backend.worker_service.app.worker)")


def one(db, sql):
    with my(db) as c, c.cursor() as cur:
        cur.execute(sql)
        return cur.fetchone()


def scenario_positive(tok):
    print("== positive: gapped primary keys, 2500 rows")
    ddl = """CREATE TABLE smoke_owner (OwnerID INT PRIMARY KEY, Username VARCHAR(50), Name VARCHAR(50),
             EmailID VARCHAR(80), Note VARCHAR(200))"""
    for db in (SRC_DB, TGT_DB):
        with my(db) as c, c.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS smoke_owner"); cur.execute(ddl)
    with my(SRC_DB) as c, c.cursor() as cur:
        rows = [(10000 + i * 3, f"u{i}", f"Name {i}", f"e{i}@example.com", "ünïcödé ✓" if i % 7 == 0 else None)
                for i in range(1, 2501)]
        cur.executemany("INSERT INTO smoke_owner VALUES (%s,%s,%s,%s,%s)", rows)
    src, tgt = get_connection(tok, "smoke-src", SRC_DB), get_connection(tok, "smoke-tgt", TGT_DB)
    step("connections ready")
    job = run_job(tok, src, tgt, "smoke_owner", "OwnerID")
    if job["status"] != "completed":
        raise Fail(f"job ended as {job['status']}: {job.get('last_error')}")
    q = "SELECT COUNT(*), MIN(OwnerID), MAX(OwnerID), SUM(CRC32(CONCAT_WS('|',OwnerID,Username,Name,EmailID,IFNULL(Note,'~')))) FROM smoke_owner"
    a, b = one(SRC_DB, q), one(TGT_DB, q)
    step(f"source={a} target={b}")
    if a != b:
        raise Fail(f"target differs from source: {a} vs {b}")
    print("   PASS")


def scenario_negative(tok):
    print("== negative: value too long for target column must fail loudly")
    with my(SRC_DB) as c, c.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS smoke_trunc"); cur.execute("CREATE TABLE smoke_trunc (id INT PRIMARY KEY, v VARCHAR(50))")
        cur.execute("INSERT INTO smoke_trunc VALUES (1,'ok'),(2,'this value is far too long for the target column')")
    with my(TGT_DB) as c, c.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS smoke_trunc"); cur.execute("CREATE TABLE smoke_trunc (id INT PRIMARY KEY, v VARCHAR(5))")
    src, tgt = get_connection(tok, "smoke-src", SRC_DB), get_connection(tok, "smoke-tgt", TGT_DB)
    job = run_job(tok, src, tgt, "smoke_trunc", "id")
    if job["status"] != "failed":
        raise Fail(f"job should have FAILED but is {job['status']} (silent truncation?)")
    err = (job.get("last_error") or "")
    r = api("GET", f"/jobs/{job['id']}/chunks", tok)
    if r.status_code == 200:
        try:
            body = r.json()
            items = body if isinstance(body, list) else body.get("chunks", body.get("items", []))
            err += " " + " ".join(str(c.get("last_error") or "") for c in items)
        except Exception:
            pass
    n = one(TGT_DB, "SELECT COUNT(*) FROM smoke_trunc")[0]
    step(f"job failed as expected; target rows={n}; error mentions: {err.strip()[:120]!r}")
    if n != 0:
        raise Fail(f"target has {n} rows after a failed chunk (partial/truncated write)")
    low = err.lower()
    if "too long" not in low and "1406" not in low:
        raise Fail("failure reason does not mention the data-too-long error (error text lost?)")
    print("   PASS")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", choices=["positive", "negative", "all"], default="positive")
    a = ap.parse_args()
    try:
        h = requests.get(API + "/health", timeout=10).json()
        step(f"API up, redis={h.get('redis')}, router_failures={h.get('router_failures')}")
        if h.get("redis") != "ok":
            raise Fail("Redis is not reachable from the API")
        if h.get("router_failures"):
            raise Fail(f"{h['router_failures']} router(s) failed to load: see GET /health/routers")
        tok = login()
        if a.scenario in ("positive", "all"):
            scenario_positive(tok)
        if a.scenario in ("negative", "all"):
            scenario_negative(tok)
    except Fail as e:
        print(f"\nFAIL: {e}")
        sys.exit(1)
    except requests.RequestException as e:
        print(f"\nFAIL: cannot reach the API at {API}: {e}")
        sys.exit(1)
    print("\nALL SMOKE TESTS PASSED")


if __name__ == "__main__":
    main()
