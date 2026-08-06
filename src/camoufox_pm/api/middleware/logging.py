"""Middleware for logging HTTP requests."""

import time
from collections.abc import Callable

from fastapi import Request, Response
from loguru import logger
from starlette.middleware.base import BaseHTTPMiddleware


class LoggingMiddleware(BaseHTTPMiddleware):
    """Log each API request with its method, path, status, and duration."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        start_time = time.time()
        client = request.client.host if request.client else "unknown"
        logger.info(f"{request.method} {request.url.path} from {client}")

        try:
            response = await call_next(request)
            process_time = time.time() - start_time
            logger.info(
                f"{request.method} {request.url.path} -> {response.status_code} ({process_time:.3f}s)"
            )
            response.headers["X-Process-Time"] = str(process_time)
            return response
        except Exception as exc:
            process_time = time.time() - start_time
            logger.error(f"{request.method} {request.url.path} ERROR: {exc} ({process_time:.3f}s)")
            raise
