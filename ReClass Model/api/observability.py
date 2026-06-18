"""Operational observability: structured request logging and Prometheus metrics."""

from __future__ import annotations

import json
import logging
import time
from typing import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger("reclass.api")


class RequestMetrics:
    """In-process counters for health and Prometheus export."""

    def __init__(self) -> None:
        self.requests_total = 0
        self.errors_total = 0
        self.latency_ms_sum = 0.0

    def record(self, *, status_code: int, latency_ms: float) -> None:
        self.requests_total += 1
        self.latency_ms_sum += latency_ms
        if status_code >= 500:
            self.errors_total += 1

    def prometheus_text(self) -> str:
        avg = (
            self.latency_ms_sum / self.requests_total
            if self.requests_total
            else 0.0
        )
        lines = [
            "# HELP reclass_http_requests_total Total HTTP requests served.",
            "# TYPE reclass_http_requests_total counter",
            f"reclass_http_requests_total {self.requests_total}",
            "# HELP reclass_http_errors_total Total HTTP 5xx responses.",
            "# TYPE reclass_http_errors_total counter",
            f"reclass_http_errors_total {self.errors_total}",
            "# HELP reclass_http_latency_ms_avg Rolling average request latency in ms.",
            "# TYPE reclass_http_latency_ms_avg gauge",
            f"reclass_http_latency_ms_avg {avg:.4f}",
        ]
        return "\n".join(lines) + "\n"


class StructuredLoggingMiddleware(BaseHTTPMiddleware):
    """Emit one JSON log line per request with method, path, status, and latency."""

    def __init__(self, app, metrics: RequestMetrics) -> None:
        super().__init__(app)
        self._metrics = metrics

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        start = time.perf_counter()
        response = await call_next(request)
        latency_ms = (time.perf_counter() - start) * 1000.0
        self._metrics.record(status_code=response.status_code, latency_ms=latency_ms)
        log_record = {
            "event": "http_request",
            "method": request.method,
            "path": request.url.path,
            "status": response.status_code,
            "latency_ms": round(latency_ms, 2),
        }
        logger.info(json.dumps(log_record))
        return response
