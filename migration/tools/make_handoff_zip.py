#!/usr/bin/env python3
r"""
make_handoff_zip.py - builds the clean zip you upload at the start of every AI session.

WHERE:  E:\Project\Migration\migration\tools\make_handoff_zip.py
RUN:    python tools\make_handoff_zip.py --backend E:\Project\Migration\migration ^
            --frontend E:\Project\Migration\frontend --out handoff.zip

Excludes: .env files (SECRETS), node_modules, venv, __pycache__, dist, .git, logs, *.pyc, big dumps.
Fails loudly if it still finds something that looks like a secret.
"""
import argparse, os, re, sys, zipfile

SKIP_DIRS = {'node_modules', 'venv', '.venv', '__pycache__', 'dist', 'build', '.git', '.idea', '.vscode', '.pytest_cache', 'logs'}
SKIP_FILES = {'.env', '.env.local', '.env.production', '.env.backup'}
SKIP_EXT = ('.pyc', '.log', '.dump', '.bak', '.zip')
MAX_BYTES = 2_000_000
# Only quoted literals that look like real secrets (not variable names / function calls).
SECRET = re.compile(r'(?im)^(?!.*(?:environ|getenv|_key\b|example|change|your-|placeholder|<|\$\{)).*?(?:password|secret|token|api_?key)\w*\s*[=:]\s*["\'][^"\'\s{}()$]{12,}["\']')

def walk(root, prefix, z, warnings):
    n = 0
    for d, dirs, files in os.walk(root):
        dirs[:] = [x for x in dirs if x not in SKIP_DIRS]
        for f in files:
            if f in SKIP_FILES or (f.startswith('.env') and f != '.env.example') or f.endswith(SKIP_EXT):
                continue
            p = os.path.join(d, f)
            if os.path.getsize(p) > MAX_BYTES:
                warnings.append(f'skipped (>2MB): {p}')
                continue
            if f.endswith(('.py', '.ts', '.tsx', '.js', '.json', '.yml', '.yaml', '.md', '.sql', '.env.example', '.txt', '.ps1', '.sh')):
                try:
                    txt = open(p, encoding='utf-8', errors='ignore').read()
                    if f != '.env.example' and SECRET.search(txt) and 'example' not in f and 'test' not in f.lower():
                        warnings.append(f'POSSIBLE SECRET (included - check!): {p}')
                except OSError:
                    pass
            z.write(p, os.path.join(prefix, os.path.relpath(p, root)))
            n += 1
    return n

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--backend', required=True)
    ap.add_argument('--frontend', required=True)
    ap.add_argument('--out', default='handoff.zip')
    ap.add_argument('--extra', nargs='*', default=[], help='extra files, e.g. PROJECT_STATE.md AI_EXECUTION_PLAYBOOK.md')
    a = ap.parse_args()
    warnings = []
    with zipfile.ZipFile(a.out, 'w', zipfile.ZIP_DEFLATED) as z:
        b = walk(a.backend, 'backend_root', z, warnings)
        f = walk(a.frontend, 'frontend_root', z, warnings)
        for e in a.extra:
            if os.path.exists(e):
                z.write(e, os.path.basename(e))
    print(f'wrote {a.out}: {b} backend files, {f} frontend files')
    for w in warnings:
        print('WARNING:', w)
    if any(w.startswith('POSSIBLE SECRET') for w in warnings):
        print('\nReview the warnings above before uploading. Never upload real credentials.')
        sys.exit(2)

if __name__ == '__main__':
    main()
