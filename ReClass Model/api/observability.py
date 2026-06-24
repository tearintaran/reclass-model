"""Operational observability: structured request logging and Prometheus metrics."""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from .settings import Settings

logger = logging.getLogger("reclass.api")


class RequestMetrics:
    """In-process counters for health and Prometheus export."""

    def __init__(self) -> None:
        self.requests_total = 0
        self.errors_total = 0
        self.latency_ms_sum = 0.0
        self.failed_evidence_resolution_total = 0
        self.security_events_total = 0
        self.slo_gauges: dict[str, float] = {}

    def record(self, *, status_code: int, latency_ms: float) -> None:
        self.requests_total += 1
        self.latency_ms_sum += latency_ms
        if status_code >= 500:
            self.errors_total += 1

    def record_failed_evidence_resolution(self, count: int = 1) -> None:
        self.failed_evidence_resolution_total += max(0, int(count))

    def record_security_event(self) -> None:
        self.security_events_total += 1

    def set_gauge(self, name: str, value: float) -> None:
        self.slo_gauges[name] = float(value)

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
            "# HELP reclass_failed_evidence_resolution_total Failed provider/evidence resolution events.",
            "# TYPE reclass_failed_evidence_resolution_total counter",
            f"reclass_failed_evidence_resolution_total {self.failed_evidence_resolution_total}",
            "# HELP reclass_security_events_total Structured security events recorded.",
            "# TYPE reclass_security_events_total counter",
            f"reclass_security_events_total {self.security_events_total}",
        ]
        for name, value in sorted(self.slo_gauges.items()):
            lines.extend([
                f"# TYPE {name} gauge",
                f"{name} {value:.4f}",
            ])
        return "\n".join(lines) + "\n"


def _parse_timestamp(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _newest_manifest_mtime(path: Path) -> float | None:
    if path.is_file():
        return path.stat().st_mtime
    if not path.is_dir():
        return None
    mtimes = [
        p.stat().st_mtime
        for p in path.iterdir()
        if p.is_file()
        and (
            p.name.endswith(".manifest.json")
            or p.name == "manifest.json"
            or p.name.endswith("_manifest.json")
        )
    ]
    return max(mtimes) if mtimes else None


def _store_rows(store, attr: str) -> list[dict]:
    data = getattr(store, attr, {})
    if isinstance(data, dict):
        rows = []
        for tenant_rows in data.values():
            if isinstance(tenant_rows, list):
                rows.extend(tenant_rows)
        return rows
    return []


def collect_slo_gauges(settings: Settings, *, store=None, base_path: str | Path | None = None) -> dict[str, float]:
    """Collect SLO-oriented gauges from local artifacts and in-memory stores."""
    base = Path(base_path) if base_path is not None else Path.cwd()
    now = datetime.now(timezone.utc)
    gauges: dict[str, float] = {}

    manifest_path = Path(settings.provider_cache_manifest_path)
    if not manifest_path.is_absolute():
        manifest_path = base / manifest_path
    newest = _newest_manifest_mtime(manifest_path)
    gauges["reclass_provider_cache_manifest_age_seconds"] = (
        time.time() - newest if newest is not None else -1.0
    )

    restore_path = Path(settings.restore_test_metadata_path)
    if not restore_path.is_absolute():
        restore_path = base / restore_path
    restore_age = -1.0
    if restore_path.is_file():
        try:
            payload = json.loads(restore_path.read_text(encoding="utf-8"))
            tested = _parse_timestamp(payload.get("tested_at"))
            if tested is not None:
                restore_age = (now - tested).total_seconds()
        except (OSError, json.JSONDecodeError):
            restore_age = -1.0
    gauges["reclass_restore_test_age_seconds"] = restore_age

    queue_rows = _store_rows(store, "_reanalysis_queue") if store is not None else []
    pending = [row for row in queue_rows if row.get("state", "pending") in {"pending", "running"}]
    enqueued: list[datetime] = []
    for row in pending:
        ts = _parse_timestamp(row.get("enqueued_at"))
        if ts is not None:
            enqueued.append(ts)
    gauges["reclass_reanalysis_lag_seconds"] = (
        (now - min(enqueued)).total_seconds() if enqueued else 0.0
    )

    alert_rows = _store_rows(store, "_alerts") if store is not None else []
    gauges["reclass_alert_backlog"] = float(
        len([row for row in alert_rows if row.get("state", "open") not in {"resolved", "dismissed"}])
    )
    return gauges


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
