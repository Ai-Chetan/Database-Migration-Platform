"""
Enterprise Security + SaaS Service — FastAPI Application
File: migration/backend/enterprise/security_main.py

Phase 10 Part 2 microservice. Runs on port 8005.

Start:
    cd migration/
    uvicorn backend.enterprise.security_main:app --host 0.0.0.0 --port 8005 --reload

Docs: http://localhost:8005/docs

Environment variables required:
    JWT_SECRET              Secret key for JWT signing (change in production)
    JWT_EXPIRE_HOURS        Token lifetime in hours (default: 24)
    MIGRATION_ENCRYPTION_KEY Fernet key for password/secret encryption

Generate keys:
    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

ALL ENDPOINTS:

── AUTHENTICATION ─────────────────────────────────────────────────────────────
    POST   /auth/register              Create tenant + admin account
    POST   /auth/login                 Login → get JWT token
    POST   /auth/logout                Revoke session
    GET    /auth/me                    Current user info + permissions
    POST   /auth/invite                Invite user to tenant
    POST   /auth/invite/accept         Accept invitation → create account
    GET    /auth/invitations           List pending invitations
    POST   /auth/api-keys              Create API key for machine access
    GET    /auth/api-keys              List API keys (prefixes only)
    DELETE /auth/api-keys/{id}         Revoke API key

── TENANTS & USERS ────────────────────────────────────────────────────────────
    GET    /tenants/{id}               Tenant detail
    GET    /tenants/{id}/users         List users in tenant
    PUT    /tenants/{id}/users/{uid}/role  Change user role
    DELETE /tenants/{id}/users/{uid}   Deactivate user
    GET    /tenants/{id}/usage         Usage statistics (jobs, rows, API calls)
    GET    /tenants/{id}/limits        Check plan limits

── APPROVAL WORKFLOW ──────────────────────────────────────────────────────────
    POST   /jobs/{id}/approval/request  Request approval before running
    POST   /jobs/{id}/approval/approve  Approve (admin only)
    POST   /jobs/{id}/approval/reject   Reject with reason (admin only)
    GET    /jobs/{id}/approval          Get approval status
    GET    /approvals/pending           List all pending approvals

── MIGRATION TEMPLATES ────────────────────────────────────────────────────────
    POST   /templates                  Save migration config as template
    GET    /templates                  List templates (tenant + public)
    GET    /templates/{id}             Get template detail
    POST   /templates/{id}/apply/{job} Apply template to a job
    DELETE /templates/{id}             Delete template

── AUDIT TRAIL ────────────────────────────────────────────────────────────────
    GET    /audit/logs                 Query audit logs with filters
    GET    /audit/logs/resource/{type}/{id}  All events for one resource
    GET    /audit/summary              Action counts by type

── SECRETS MANAGER ────────────────────────────────────────────────────────────
    POST   /secrets/{key}              Store/update encrypted secret
    GET    /secrets                    List key names (never values)
    DELETE /secrets/{key}              Delete a secret
    POST   /secrets/{key}/verify       Verify value without revealing it
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.enterprise.routers import (
    auth,
    tenants,
    approvals,
    templates,
    audit,
    secrets,
)

app = FastAPI(
    title="Migration Platform — Enterprise Security & SaaS",
    description=(
        "Phase 10 Part 2: Enterprise security and SaaS foundation. "
        "JWT authentication, 7-role RBAC, encrypted secrets, "
        "immutable audit trail, multi-tenancy, user invitations, "
        "migration approval workflow, and reusable migration templates."
    ),
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(tenants.router)
app.include_router(approvals.router)
app.include_router(templates.router)
app.include_router(audit.router)
app.include_router(secrets.router)


@app.get("/health", tags=["Health"])
def health():
    return {
        "status":  "ok",
        "service": "enterprise_security_saas",
        "port":    8005,
        "version": "1.0.0",
    }
