#!/bin/bash
# sandbox_bootstrap.sh - run INSIDE the AI session's Linux sandbox (not on your Windows PC).
# Starts PostgreSQL + Redis + MariaDB(MySQL) and installs Python deps so the platform can be
# really run and tested. Idempotent. Verified in the audit session (Ubuntu 24.04).
#
# usage:  bash sandbox_bootstrap.sh <backend_root> [schema_dump.sql]
#   <backend_root>  folder containing backend/ and .env  (e.g. /mnt/user-data/uploads/x/backend_root)
#   schema_dump     optional pg_dump file (UTF-16 or UTF-8) used to create the metadata DB
set -u
BE="${1:?backend root}"; DUMP="${2:-}"
export DEBIAN_FRONTEND=noninteractive
if ! command -v psql >/dev/null; then
  apt-get update -q >/dev/null 2>&1
  apt-get install -y -q postgresql redis-server mariadb-server >/tmp/apt.log 2>&1 || { tail -5 /tmp/apt.log; exit 1; }
fi
REQ="$BE/requirements.txt"; [ -f "$REQ" ] || REQ="$(dirname "$0")/requirements.txt"
pip install -q --break-system-packages -r "$REQ" pyflakes pytest 2>&1 | tail -1

service postgresql start >/dev/null 2>&1
(redis-cli ping >/dev/null 2>&1) || redis-server --daemonize yes >/dev/null
mkdir -p /run/mysqld; chown mysql:mysql /run/mysqld
if ! mysqladmin ping >/dev/null 2>&1; then
  rm -f /run/mysqld/mysqld.sock /run/mysqld/mysqld.pid
  setsid nohup mysqld_safe --user=mysql >/tmp/mysqld.log 2>&1 < /dev/null &
fi
for i in $(seq 1 20); do mysqladmin ping >/dev/null 2>&1 && break; sleep 1; done

su postgres -c "psql -qc \"ALTER USER postgres PASSWORD 'pgpw'\"" >/dev/null 2>&1
mysql -uroot -e "CREATE USER IF NOT EXISTS 'mig'@'%' IDENTIFIED BY 'migpw'; GRANT ALL ON *.* TO 'mig'@'%'; FLUSH PRIVILEGES;" 2>/dev/null

if [ -n "$DUMP" ] && ! su postgres -c "psql -lqt" | grep -q migration_metadata; then
  su postgres -c "psql -qc 'CREATE DATABASE migration_metadata'"
  python3 - "$DUMP" <<'PY'
import sys,re
raw=open(sys.argv[1],'rb').read()
t=raw.decode('utf-16') if raw[:2] in (b'\xff\xfe',b'\xfe\xff') else raw.decode('utf-8','replace')
t=t.replace('\r','')
t='\n'.join(l for l in t.split('\n') if not re.match(r'\\(un)?restrict|SET transaction_timeout|DROP DATABASE|CREATE DATABASE|\\connect',l))
open('/tmp/schema_clean.sql','w').write(t)
PY
  su postgres -c "psql -q -d migration_metadata -f /tmp/schema_clean.sql" >/tmp/schema_load.log 2>&1
fi
# point .env at the sandbox services
if [ -f "$BE/.env" ]; then
  sed -i 's/^POSTGRES_PASSWORD=.*/POSTGRES_PASSWORD=pgpw/; s/^REDIS_PASSWORD=.*/REDIS_PASSWORD=/' "$BE/.env"
fi
echo "postgres: $(su postgres -c "psql -Atc 'select 1'" 2>&1 | head -1)  redis: $(redis-cli ping 2>&1)  mysql: $(mysqladmin ping 2>&1)"
echo "API:    cd $BE && PYTHONPATH=\$PWD setsid nohup python3 -m uvicorn backend.main:app --port 8000 > /tmp/api.log 2>&1 < /dev/null &"
echo "WORKER: cd $BE && PYTHONPATH=\$PWD WORKER_ID=w1 setsid nohup python3 -m backend.worker_service.app.worker > /tmp/worker.log 2>&1 < /dev/null &"
echo "NOTE: always start long-running processes with setsid nohup ... & (a foreground server kills the tool call)."
