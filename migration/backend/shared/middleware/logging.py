import time
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from backend.shared.config.logging import logger

class LoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start_time = time.time()
        request_id = getattr(request.state, "request_id", None)
        
        logger.info(
            "Request start",
            method=request.method,
            url=str(request.url),
            request_id=request_id
        )

        response = await call_next(request)
        
        process_time = time.time() - start_time
        
        logger.info(
            "Request end",
            method=request.method,
            url=str(request.url),
            status_code=response.status_code,
            response_time=f"{process_time:.4f}s",
            request_id=request_id
        )
        
        return response
