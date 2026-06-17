from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from backend.shared.config.security import verify_token
from backend.shared.exceptions.auth import AuthenticationException

class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Allow open paths to bypass
        if hasattr(request, "url"):
            if "health" in request.url.path or "login" in request.url.path:
                return await call_next(request)

        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            # You might want to handle this natively in fastapi routes,
            # but as a middleware it intercepts all.
            # Using Starlette responses for raw error handling, or let route deps handle it contextually.
            pass
        else:
            token = auth_header.split(" ")[1]
            if not verify_token(token):
                pass # Usually handled via FastAPI dependencies better than middleware
        
        response = await call_next(request)
        return response
