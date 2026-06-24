"""HTTP guard middleware: request-size and in-process rate limiting."""

from __future__ import annotations

import time
from collections import defaultdict, deque
from typing import Callable, Deque, Dict, Tuple

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response


class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
    """Reject requests whose declared Content-Length exceeds the configured cap."""

    def __init__(self, app, *, max_bytes: int) -> None:
        super().__init__(app)
        self._max_bytes = max(0, int(max_bytes))

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if self._max_bytes:
            raw = request.headers.get("content-length")
            if raw is not None:
                try:
                    size = int(raw)
                except ValueError:
                    size = 0
                if size > self._max_bytes:
                    return JSONResponse(
                        status_code=413,
                        content={
                            "detail": "request body too large",
                            "max_bytes": self._max_bytes,
                        },
                    )
        return await call_next(request)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Simple fixed-window limiter keyed by client host and HTTP path.

    This is deliberately local and dependency-free. Production deployments can
    still put a reverse proxy in front, but the API now has a fail-closed guard
    when it is exposed directly in small installations.
    """

    def __init__(self, app, *, requests_per_minute: int) -> None:
        super().__init__(app)
        self._limit = max(0, int(requests_per_minute))
        self._hits: Dict[Tuple[str, str], Deque[float]] = defaultdict(deque)

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if not self._limit:
            return await call_next(request)
        now = time.monotonic()
        host = request.client.host if request.client else "unknown"
        key = (host, request.url.path)
        hits = self._hits[key]
        cutoff = now - 60.0
        while hits and hits[0] < cutoff:
            hits.popleft()
        if len(hits) >= self._limit:
            retry_after = max(1, int(60.0 - (now - hits[0]))) if hits else 60
            return JSONResponse(
                status_code=429,
                headers={"Retry-After": str(retry_after)},
                content={
                    "detail": "rate limit exceeded",
                    "limit": self._limit,
                    "window_seconds": 60,
                },
            )
        hits.append(now)
        return await call_next(request)
