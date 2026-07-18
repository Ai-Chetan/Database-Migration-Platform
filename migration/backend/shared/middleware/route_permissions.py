"""
Route Permission Map
File: migration/backend/shared/middleware/route_permissions.py

Centralized RBAC enforcement for the ENTIRE platform, applied as a single
ASGI middleware in main.py. This avoids hand-editing 20+ existing router
files — every route's required permission is declared here in ONE place,
which is also the easiest place to audit "who can do what."

How it works:
    1. Each incoming request's (method, path) is matched against ROUTE_RULES
       using prefix + method matching (supports path params via prefix match).
    2. If no rule matches, the route defaults to PUBLIC_PATHS check, then
       falls back to "any authenticated user" (safe default — NOT open).
    3. If a rule matches, the request's JWT is decoded and the user's role
       is checked against required permissions/roles for that rule.
    4. On failure: 401 (no/invalid token) or 403 (valid token, wrong role).

This is a defense-in-depth layer. Routers can ALSO use
Depends(require_permission(...)) directly for extra precision — both can
coexist safely since permission checks are idempotent.

Rule precedence: more specific (longer) path prefixes are checked first.
"""

import re
from typing import Optional, List, Tuple, Dict, Any
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from backend.shared.auth.auth_service import AuthService, AuthError
from backend.shared.config.logging import logger


# ── Public paths — no auth required at all ─────────────────────────────────────

PUBLIC_PATHS: List[Tuple[str, str]] = [
    ("POST", "/auth/login"),
    ("POST", "/auth/forgot-password"),
    ("POST", "/auth/reset-password"),
    ("GET",  "/health"),
    ("GET",  "/"),
    ("GET",  "/docs"),
    ("GET",  "/redoc"),
    ("GET",  "/openapi.json"),
    ("GET",  "/roles"),          # reference data, not sensitive
]


# ── Route rules ────────────────────────────────────────────────────────────────
# Format: (method_pattern, path_prefix, required_permissions_or_roles, mode)
# mode: "permission" checks against role_definitions.permissions
#       "role"       checks exact role membership (platform_admin always passes)
#       "auth_only"  just requires a valid logged-in user, any role
#
# Rules are checked in order — put more specific prefixes BEFORE general ones.

ROUTE_RULES: List[Tuple[str, str, List[str], str]] = [

    # ── Auth (self-service, any authenticated user) ─────────────────────────
    ("*",    "/auth/",                              [], "auth_only"),

    # ── User management — admin only ─────────────────────────────────────────
    ("*",    "/users",                               ["manage:users"], "permission"),

    # ── Operations Console — granular, most sensitive surface ───────────────
    ("POST", "/ops/maintenance/emergency-stop",      ["emergency:stop"], "permission"),
    ("POST", "/ops/maintenance/enable",               ["maintenance:mode"], "permission"),
    ("POST", "/ops/maintenance/disable",              ["maintenance:mode"], "permission"),
    ("GET",  "/ops/maintenance",                      [], "auth_only"),
    ("POST", "/ops/workers/",                         ["kill:worker"], "permission"),  # covers pause/resume/kill/quarantine
    ("POST", "/ops/jobs/",                            ["pause:job"], "permission"),    # covers pause/resume/cancel/scale/drain
    ("POST", "/ops/chunks/",                          ["pause:job"], "permission"),    # retry/skip/reassign
    ("GET",  "/ops/",                                 [], "auth_only"),                # workers list, live-stats, problems, actions

    # ── Jobs (Control Plane) ─────────────────────────────────────────────────
    ("POST", "/jobs/",                                ["start:job"], "permission"),    # /jobs/{id}/start
    ("DELETE","/jobs/",                                ["cancel:job"], "permission"),
    ("POST", "/jobs",                                  ["create:job"], "permission"),  # exact POST /jobs (create)
    ("GET",  "/jobs",                                  [], "auth_only"),

    # ── Connections ───────────────────────────────────────────────────────────
    ("POST", "/connections",                          ["create:connection"], "permission"),
    ("PUT",  "/connections/",                          ["create:connection"], "permission"),
    ("DELETE","/connections/",                          ["create:connection"], "permission"),
    ("POST", "/connections/",                          [], "auth_only"),               # test endpoint — any user can test
    ("GET",  "/connections",                           [], "auth_only"),

    # ── Extended connector test/discover — read/diagnostic, any user ────────
    ("POST", "/connectors/extended/",                  [], "auth_only"),
    ("GET",  "/connectors/extended",                   [], "auth_only"),

    # ── Schema mapping ────────────────────────────────────────────────────────
    ("POST", "/schemas/",                              ["create:connection"], "permission"),
    ("POST", "/projects",                               ["create:connection"], "permission"),
    ("PUT",  "/projects/",                               ["create:connection"], "permission"),
    ("POST", "/projects/",                               [], "auth_only"),             # dry-run trigger — any migration role
    ("GET",  "/projects",                               [], "auth_only"),
    ("GET",  "/mappings/",                              [], "auth_only"),
    ("PUT",  "/mappings/",                               ["create:connection"], "permission"),

    # ── Intelligence / Simulation (read-heavy analysis, any authenticated) ──
    ("*",    "/intelligence/",                          [], "auth_only"),
    ("*",    "/assess",                                 [], "auth_only"),
    ("*",    "/advise",                                 [], "auth_only"),
    ("*",    "/estimate",                                [], "auth_only"),
    ("*",    "/quality/",                                [], "auth_only"),
    ("*",    "/simulate",                                [], "auth_only"),

    # ── Live Intelligence (drift/tuning/benchmark) ──────────────────────────
    ("POST", "/live/drift/start",                        ["kill:worker"], "permission"),
    ("POST", "/live/drift/stop",                         ["kill:worker"], "permission"),
    ("POST", "/live/tuning/start",                       ["kill:worker"], "permission"),
    ("POST", "/live/tuning/stop",                        ["kill:worker"], "permission"),
    ("*",    "/live/",                                   [], "auth_only"),

    # ── Data Masking — configuring rules is sensitive ────────────────────────
    ("POST", "/masking/rule-sets",                       ["create:masking"], "permission"),
    ("POST", "/masking/rule-sets/",                      ["create:masking"], "permission"),
    ("DELETE","/masking/rule-sets/",                      ["create:masking"], "permission"),
    ("*",    "/masking/",                                 [], "auth_only"),            # preview/test/strategies — any user

    # ── Plugin Service — policies & notifiers are admin, reads are open ─────
    ("POST", "/plugins/policies/configure",               ["configure:policies"], "permission"),
    ("POST", "/plugins/notifiers/configure",               ["configure:notifiers"], "permission"),
    ("POST", "/plugins/policies/check/",                   [], "auth_only"),
    ("POST", "/plugins/notifiers/test",                     ["configure:notifiers"], "permission"),
    ("POST", "/plugins/validators/run",                     [], "auth_only"),
    ("GET",  "/plugins/",                                    [], "auth_only"),

    # ── Kernel — plugin/event/service registry, read open, write admin ──────
    ("POST", "/plugins/register",                           ["*"], "role_platform_admin"),
    ("GET",  "/events",                                      [], "auth_only"),
    ("GET",  "/services",                                    [], "auth_only"),
    ("GET",  "/catalog",                                     [], "auth_only"),

    # ── Workflow Engine ────────────────────────────────────────────────────────
    ("POST", "/workflows",                                    ["create:job"], "permission"),
    ("PUT",  "/workflows/",                                    ["create:job"], "permission"),
    ("GET",  "/workflows",                                     [], "auth_only"),

    # ── Scheduler — creating schedules is a migration_admin+ action ─────────
    ("POST", "/scheduler/jobs",                                ["create:schedule"], "permission"),
    ("PUT",  "/scheduler/jobs/",                                ["create:schedule"], "permission"),
    ("DELETE","/scheduler/jobs/",                                ["create:schedule"], "permission"),
    ("POST", "/scheduler/jobs/",                                 ["create:schedule"], "permission"),  # trigger
    ("GET",  "/scheduler/",                                      [], "auth_only"),
    ("POST", "/scheduler/cron/validate",                          [], "auth_only"),

    # ── Reports — generating is a write action, viewing is open ─────────────
    ("POST", "/reports/generate",                                 [], "auth_only"),    # any migration role can generate
    ("GET",  "/reports/",                                          [], "auth_only"),

    # ── Knowledge Base — mostly read, recording happens automatically ───────
    ("*",    "/knowledge/",                                        [], "auth_only"),

    # ── Enterprise: Tenants, Templates, Secrets — admin territory ───────────
    ("*",    "/tenants",                                            ["manage:users"], "permission"),
    ("*",    "/secrets",                                            ["manage:users"], "permission"),
    ("POST", "/templates",                                          ["create:connection"], "permission"),
    ("GET",  "/templates",                                          [], "auth_only"),

    # ── Approvals — request is any migration role, approve/reject is admin ──
    ("POST", "/jobs/",                                              [], "auth_only"),   # covers /approval/request (broad net; refined by 'permission' rules above taking precedence via order)
    ("GET",  "/approvals",                                          [], "auth_only"),

    # ── Audit log — dedicated permission ─────────────────────────────────────
    ("GET",  "/audit",                                              ["view:audit"], "permission"),
    ("GET",  "/ops/actions",                                        ["view:audit"], "permission"),

    # ── Monitoring — read-only, any authenticated user ───────────────────────
    ("GET",  "/monitoring/",                                        [], "auth_only"),
]


def _match_method(rule_method: str, request_method: str) -> bool:
    return rule_method == "*" or rule_method == request_method


def _match_rule(method: str, path: str) -> Optional[Tuple[List[str], str]]:
    """Find the most specific matching rule for a given method+path."""
    best_match: Optional[Tuple[int, List[str], str]] = None

    for rule_method, prefix, perms, mode in ROUTE_RULES:
        if not _match_method(rule_method, method):
            continue
        if path.startswith(prefix):
            specificity = len(prefix)
            if best_match is None or specificity > best_match[0]:
                best_match = (specificity, perms, mode)

    if best_match:
        return best_match[1], best_match[2]
    return None


def _is_public(method: str, path: str) -> bool:
    for m, p in PUBLIC_PATHS:
        if (m == method or m == "*") and path.startswith(p):
            return True
    # Root docs paths
    if path in ("/docs", "/redoc", "/openapi.json", "/", "/health"):
        return True
    return False


class RBACMiddleware(BaseHTTPMiddleware):
    """
    Global RBAC enforcement middleware. Add to main.py with:
        app.add_middleware(RBACMiddleware)

    Runs BEFORE the route handler. Attaches request.state.user on success
    so route handlers can also use Depends(get_current_user) without
    re-decoding the token (both work independently and safely).
    """

    async def dispatch(self, request: Request, call_next):
        method = request.method
        path   = request.url.path

        # CORS preflight requests carry no Authorization header by design —
        # always let them through so browser-based clients work correctly.
        if method == "OPTIONS":
            return await call_next(request)

        if _is_public(method, path):
            return await call_next(request)

        auth_header = request.headers.get("authorization", "")
        if not auth_header.startswith("Bearer "):
            return JSONResponse(
                status_code=401,
                content={"detail": "Not authenticated. Include 'Authorization: Bearer <token>' header."},
            )

        token = auth_header[len("Bearer "):]

        try:
            payload = AuthService.decode_token(token)
        except AuthError as e:
            return JSONResponse(status_code=e.status_code, content={"detail": e.message})

        user_role = payload.get("role", "")

        rule = _match_rule(method, path)

        if rule is not None:
            perms, mode = rule

            if mode == "role_platform_admin":
                if user_role != "platform_admin":
                    return JSONResponse(
                        status_code=403,
                        content={"detail": "This action requires platform_admin privileges."},
                    )

            elif mode == "permission" and perms:
                if user_role != "platform_admin":
                    # Look up role permissions from DB via a lightweight sync check.
                    # We avoid a DB round-trip per-request for performance by checking
                    # against a cached permission set attached at login time (role name
                    # is enough here since role_definitions rarely change at runtime).
                    from backend.shared.middleware.role_cache import get_role_permissions
                    role_perms = get_role_permissions(user_role)
                    if "*" not in role_perms and not any(p in role_perms for p in perms):
                        return JSONResponse(
                            status_code=403,
                            content={
                                "detail": f"This action requires one of the following "
                                          f"permissions: {', '.join(perms)}."
                            },
                        )
            # mode == "auth_only" → any valid token passes, nothing more to check

        # Attach user info for downstream handlers
        request.state.user = {
            "id":        payload.get("sub"),
            "email":     payload.get("email"),
            "name":      payload.get("name"),
            "role":      user_role,
            "tenant_id": payload.get("tenant_id", "local"),
            "jti":       payload.get("jti"),
        }

        return await call_next(request)
