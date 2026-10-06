#!/usr/bin/env python3
r"""
contract_audit.py - finds backend<->database<->frontend mismatches.

WHERE TO PUT IT:  E:\Project\Migration\migration\tools\contract_audit.py

RUN (from E:\Project\Migration\migration, venv active):
    python tools\contract_audit.py --schema E:\path\migration_metadata_backup.sql ^
        --backend backend --frontend ..\frontend\src --out audit_report.md

What it checks (heuristic - regex based, so treat hits as "look here", not gospel):
  A. SQL in backend .py files: unknown tables, unknown columns, and string
     literals compared/assigned to CHECK-constrained columns that the CHECK forbids.
  B. Backend routes (@router.get/post/... + include_router prefixes) vs
     frontend API calls (api.get('/x'), axios, fetch). Reports calls with no route.
  C. Frontend status maps (e.g. { running: {...}, pending: {...} }) vs the DB CHECK
     values for status columns - the cause of 'Cannot read properties of undefined'.
Reads the pg_dump file even if PowerShell saved it as UTF-16.
r"""
import argparse, os, re, sys, collections

def read_text(p):
    raw = open(p, 'rb').read()
    for enc in ('utf-8-sig', 'utf-16'):
        try:
            t = raw.decode(enc)
            if enc == 'utf-8-sig' and '\x00' in t[:200]:
                continue
            return t.replace('\r', '')
        except UnicodeError:
            pass
    return raw.decode('latin-1').replace('\r', '')

def parse_schema(path):
    t = read_text(path)
    tables, checks = {}, collections.defaultdict(dict)
    for name, body in re.findall(r'CREATE TABLE public\.(\w+) \((.*?)\n\);', t, re.S):
        cols = []
        for line in body.split('\n'):
            s = line.strip().rstrip(',')
            if not s: continue
            if s.startswith('CONSTRAINT'):
                m = re.search(r'CHECK \(\(\((\w+)\)::text = ANY \(\(?ARRAY\[(.*?)\]', s)
                if m:
                    vals = re.findall(r"'([^']+)'", m.group(2))
                    checks[name][m.group(1)] = set(vals)
                continue
            if s.startswith(('PRIMARY', 'UNIQUE', 'CHECK')): continue
            cols.append(s.split()[0].strip('"'))
        tables[name] = set(cols)
    return tables, checks

def walk(root, exts):
    for d, _, fs in os.walk(root):
        if any(x in d for x in ('node_modules', '__pycache__', '.git', 'venv', 'dist')): continue
        for f in fs:
            if f.endswith(exts): yield os.path.join(d, f)


def audit_sql(backend, tables, checks, issues):
    for p in walk(backend, ('.py',)):
        src = read_text(p)
        for m in re.finditer(r'(?:text\(|execute\(|\()\s*(?:f?r?)("""|\'\'\'|"|\')(.*?)\1', src, re.S):
            q = m.group(2)
            if not re.search(r'\b(SELECT|INSERT\s+INTO|UPDATE|DELETE\s+FROM)\b', q, re.I): continue
            line = src[:m.start()].count('\n') + 1
            qn = re.sub(r'\s+', ' ', q)
            refs = re.findall(r'\b(?:FROM|JOIN|UPDATE|INTO)\s+(?:public\.)?(\w+)(?:\s+(?:AS\s+)?(\w+))?', qn, re.I)
            used = {}
            for t, alias in refs:
                if t.upper() in ('SELECT', 'SET', 'VALUES', 'ONLY') : continue
                if t not in tables:
                    if t.islower() and '_' in t or t in ('users', 'tenants', 'roles'):
                        issues['unknown_table'].append((p, line, t))
                    continue
                used[t] = alias
            # INSERT INTO t (a,b,c)
            for t, cols in re.findall(r'INSERT\s+INTO\s+(?:public\.)?(\w+)\s*\((.*?)\)', qn, re.I):
                if t in tables:
                    for c in [x.strip() for x in cols.split(',')]:
                        if c and c not in tables[t]: issues['unknown_column'].append((p, line, f'{t}.{c}'))
            # UPDATE t SET a=..., b=...
            for t, sets in re.findall(r'UPDATE\s+(?:public\.)?(\w+)\s+SET\s+(.*?)(?:WHERE|RETURNING|$)', qn, re.I):
                if t in tables:
                    for c in re.findall(r'(?:^|,)\s*(\w+)\s*=', sets):
                        if c not in tables[t]: issues['unknown_column'].append((p, line, f'{t}.{c}'))
            # qualified alias.col or table.col
            for ref, col in re.findall(r'\b(\w+)\.(\w+)\b', qn):
                tbl = ref if ref in tables else next((t for t, a in used.items() if a == ref), None)
                if tbl and col not in tables[tbl] and col != '*':
                    issues['unknown_column'].append((p, line, f'{tbl}.{col}'))
            # single-table SELECT cols
            m1 = re.match(r'SELECT\s+(.*?)\s+FROM\s+(?:public\.)?(\w+)\s*(?:WHERE|ORDER|GROUP|LIMIT|$)', qn, re.I)
            if m1 and m1.group(2) in tables and '*' not in m1.group(1):
                for c in re.findall(r'(?:^|,)\s*(?:\w+\()?\s*([a-z_]+)\s*\)?(?:\s+AS\s+\w+)?\s*(?=,|$)', m1.group(1), re.I):
                    if c.lower() not in tables[m1.group(2)] and c.upper() not in ('NULL', 'COUNT', 'NOW'):
                        issues['unknown_column'].append((p, line, f'{m1.group(2)}.{c}'))
            # status literals vs CHECK
            for t in used:
                for col, allowed in checks.get(t, {}).items():
                    for lit in re.findall(rf"\b{col}\b\s*(?:=|IN)\s*\(?\s*'([^']+)'", qn, re.I):
                        if lit not in allowed:
                            issues['check_violation'].append((p, line, f"{t}.{col}='{lit}' not in {sorted(allowed)}"))

def backend_routes(backend):
    routes = set(); prefixes = []
    for p in walk(backend, ('.py',)):
        src = read_text(p)
        rp = re.search(r'APIRouter\([^)]*prefix\s*=\s*["\']([^"\']*)', src)
        base = rp.group(1) if rp else ''
        for meth, path in re.findall(r'@\w+\.(get|post|put|patch|delete)\(\s*["\']([^"\']*)', src):
            routes.add((meth.upper(), (base + path)))
        for pre in re.findall(r'include_router\([^)]*prefix\s*=\s*["\']([^"\']*)', src):
            prefixes.append(pre)
    return routes, prefixes

def norm(path):
    path = path.split('?')[0]
    path = re.sub(r'\$\{[^}]*\}|\{[^}]*\}|:\w+', '{}', path)
    return path.rstrip('/') or '/'

def audit_routes(backend, frontend, issues):
    routes, prefixes = backend_routes(backend)
    known = {(m, norm(p)) for m, p in routes}
    known |= {(m, norm(pre + p)) for m, p in routes for pre in prefixes}
    known_paths = {p for _, p in known}
    for p in walk(frontend, ('.ts', '.tsx', '.js', '.jsx')):
        src = read_text(p)
        for meth, path in re.findall(r'\b(?:api|axios|client|http)\.(get|post|put|patch|delete)\s*(?:<[^>]*>)?\(\s*[`"\']([^`"\']+)', src):
            n = norm(path)
            if (meth.upper(), n) not in known and n not in known_paths:
                issues['route_missing'].append((p, src[:src.find(path)].count('\n') + 1, f'{meth.upper()} {path}'))

def audit_status_maps(frontend, checks, issues):
    union = set()
    for t in ('migration_jobs', 'migration_tables', 'migration_chunks'):
        for col, vals in checks.get(t, {}).items():
            if col == 'status': union |= vals
    for p in walk(frontend, ('.ts', '.tsx')):
        src = read_text(p)
        if 'Status' not in os.path.basename(p) and 'status' not in src.lower(): continue
        for m in re.finditer(r'(?:const|export const)\s+(\w*(?:STATUS|Status)\w*)\s*(?::[^=]+)?=\s*\{(.*?)\n\}', src, re.S):
            keys = set(re.findall(r'^\s*[\'"]?(\w+)[\'"]?\s*:', m.group(2), re.M))
            line = src[:m.start()].count('\n') + 1
            if keys:
                miss = union - keys
                if miss: issues['status_map_missing_keys'].append((p, line, f'{m.group(1)} lacks {sorted(miss)}'))
                extra = {k for k in keys if k.islower()} - union
                if extra: issues['status_map_unknown_keys'].append((p, line, f'{m.group(1)} has keys not in DB CHECK: {sorted(extra)}'))
        if re.search(r'\[\s*status\s*\]\.\w+|STATUS\w*\[[^\]]+\]\.', src):
            issues['unsafe_status_lookup'].append((p, 1, 'direct MAP[status].prop with no fallback -> crashes on unknown status'))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--schema', required=True); ap.add_argument('--backend', required=True)
    ap.add_argument('--frontend', required=True); ap.add_argument('--out', default='audit_report.md')
    a = ap.parse_args()
    tables, checks = parse_schema(a.schema)
    print(f'schema: {len(tables)} tables, CHECK constraints on {sum(len(v) for v in checks.values())} columns')
    issues = collections.defaultdict(list)
    audit_sql(a.backend, tables, checks, issues)
    audit_routes(a.backend, a.frontend, issues)
    audit_status_maps(a.frontend, checks, issues)
    titles = {
        'unknown_table': 'SQL references a table that does not exist',
        'unknown_column': 'SQL references a column that does not exist',
        'check_violation': 'SQL writes/compares a value the DB CHECK constraint forbids',
        'route_missing': 'Frontend calls an endpoint the backend does not define',
        'status_map_missing_keys': 'Frontend status map missing DB statuses (render crash risk)',
        'status_map_unknown_keys': 'Frontend status map has statuses the DB can never hold',
        'unsafe_status_lookup': 'Frontend status lookup without fallback',
    }
    out = ['# Contract audit report', '']
    for k, title in titles.items():
        rows = sorted(set(issues.get(k, [])))
        out += [f'## {title} ({len(rows)})', '']
        out += [f'- `{os.path.relpath(p)}:{ln}` - {d}' for p, ln, d in rows] or ['- none found']
        out.append('')
    open(a.out, 'w', encoding='utf-8').write('\n'.join(out))
    print('written', a.out, '| totals:', {k: len(set(v)) for k, v in issues.items()})

if __name__ == '__main__':
    main()
