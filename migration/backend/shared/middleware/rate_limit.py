"""
Rate Limiting Middleware
File: migration/backend/shared/middleware/rate_limit.py

IP-based rate limiting using Redis (already a platform dependency - no new
infra required). Applied selectively to sensitive endpoints (login, password
reset) rather than globally, since aggressive rate limiting on every route
would hurt legitimate high-frequency usage (e.g., the 3-second job polling
the frontend does on Job Detail / Operations Console).

The existing account-lockout mechanism in AuthService (5 failed attempts ->
15 min lock) protects a single ACCOUNT. This middleware protects against
an attacker trying many different email addresses from one IP, which
account lockout alone does not stop.

Usage in main.py:

    from backend.shared.middleware.rate_limit import RateLimitMiddleware
    app.add_middleware(RateLimitMiddleware)

Configuration via environment variables:

    RATE_LIMIT_LOGIN_MAX       (default 10)   - max login attempts
    RATE_LIMIT_LOGIN_WINDOW    (default 60)   - per this many seconds, per IP
    RATE_LIMIT_RESET_MAX       (default 5)    - max password reset requests
    RATE_LIMIT_RESET_WINDOW    (default 300)  - per this many seconds, per IP
"""

import os
from typing import Tuple
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from backend.shared.config.logging import logger

try:
    from backend.shared.config.redis import redis_client
except Exception:
    redis_client = None


LOGIN_MAX     = int(os.environ.get("RATE_LIMIT_LOGIN_MAX", "10"))
LOGIN_WINDOW  = int(os.environ.get("RATE_LIMIT_LOGIN_WINDOW", "60"))
RESET_MAX     = int(os.environ.get("RATE_LIMIT_RESET_MAX", "5"))
RESET_WINDOW  = int(os.environ.get("RATE_LIMIT_RESET_WINDOW", "300"))


_LIMITED_PATHS = {
    ("POST", "/auth/login"):           (LOGIN_MAX, LOGIN_WINDOW),
    ("POST", "/auth/forgot-password"): (RESET_MAX, RESET_WINDOW),
    ("POST", "/auth/reset-password"):  (RESET_MAX, RESET_WINDOW),
}


def _get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _check_and_increment(key: str, max_requests: int, window_seconds: int) -> Tuple[bool, int]:
    """
    Fails OPEN (allows the request) if Redis is unavailable - availability
    of the login page matters more than rate limiting during a Redis outage,
    and account lockout still provides a backstop.
    """
    if redis_client is None:
        return True, 0

    try:
        current = redis_client.incr(key)
        if current == 1:
            redis_client.expire(key, window_seconds)
        return current <= max_requests, current
    except Exception as e:
        logger.warning("Rate limit check failed, failing open", error=str(e))
        return True, 0


class RateLimitMiddleware(BaseHTTPMiddleware):

    async def dispatch(self, request: Request, call_next):
        method = request.method
        path   = request.url.path

        limit_config = _LIMITED_PATHS.get((method, path))

        if limit_config is None:
            return await call_next(request)

        max_requests, window = limit_config
        ip  = _get_client_ip(request)
        key = f"ratelimit:{method}:{path}:{ip}"

        allowed, current_count = _check_and_increment(key, max_requests, window)

        if not allowed:
            logger.warning("Rate limit exceeded", ip=ip, path=path, count=current_count)
            return JSONResponse(
                status_code=429,
                content={
                    "detail": f"Too many requests. Please wait before trying again. "
                              f"(limit: {max_requests} per {window}s)"
                },
                headers={"Retry-After": str(window)},
            )

        return await call_next(request)
