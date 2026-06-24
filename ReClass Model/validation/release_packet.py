"""Exportable validation packet for a scoped clinical release."""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
from typing import Any, Dict, Iterable, Mapping, Optional


def _stable_json(data: Mapping[str, Any]) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), default=str)


def packet_digest(packet: Mapping[str, Any]) -> str:
    """Stable SHA-256 digest of a release packet, excluding wall-clock metadata."""
    payload = {
        key: value
        for key, value in packet.items()
        if key not in {"generated_utc", "packet_id"}
    }
    return hashlib.sha256(_stable_json(payload).encode("utf-8")).hexdigest()


def source_snapshots_from_report(report: Mapping[str, Any]) -> Dict[str, Any]:
    """Extract fixture/source snapshot summaries from an analytical report."""
    snapshots: Dict[str, Any] = {}
    for benchmark in report.get("benchmarks", []) or []:
        name = benchmark.get("benchmark")
        if not name:
            continue
        snapshots[str(name)] = benchmark.get("fixture_source_versions") or {}
    return snapshots


def benchmark_metrics_from_report(
    report: Mapping[str, Any],
    *,
    release_scope: Mapping[str, Any] | None = None,
) -> list[Dict[str, Any]]:
    """Return benchmark metric blocks, preserving scoped gates for review."""
    scope = release_scope or {}
    rows = []
    for benchmark in report.get("benchmarks", []) or []:
        rows.append({
            "benchmark": benchmark.get("benchmark"),
            "gate_pass": benchmark.get("gate_pass"),
            "case_count": benchmark.get("case_count"),
            "metrics": benchmark.get("metrics") or {},
            "scoped_gates": _filter_scoped_gates(
                benchmark.get("scoped_gates") or {},
                release_scope=scope,
            ),
        })
    return rows


def _scope_values(scope: Mapping[str, Any], *names: str) -> set[str]:
    values: set[str] = set()
    for name in names:
        raw = scope.get(name)
        if raw in (None, "", "*"):
            continue
        if isinstance(raw, str):
            values.add(raw)
        else:
            values.update(str(item) for item in raw or [])
    return {value.upper() for value in values}


def _filter_scoped_gates(
    scoped_gates: Mapping[str, Any],
    *,
    release_scope: Mapping[str, Any],
) -> Dict[str, Any]:
    wanted = {
        "gene": _scope_values(release_scope, "gene", "genes"),
        "disease": _scope_values(release_scope, "disease", "diseases", "conditions"),
        "variant_class": _scope_values(release_scope, "evidence_class", "evidence_classes"),
    }
    if not any(wanted.values()):
        return dict(scoped_gates)
    filtered: Dict[str, Any] = {}
    for scope_name, blocks in scoped_gates.items():
        targets = wanted.get(scope_name, set())
        if not targets:
            filtered[scope_name] = list(blocks or [])
            continue
        filtered[scope_name] = [
            block for block in blocks or []
            if str(block.get("scope_value", "")).upper() in targets
        ]
    return filtered


def serious_discordance_disposition(
    discordances: Iterable[Mapping[str, Any]],
) -> Dict[str, Any]:
    rows = [dict(row) for row in discordances]
    unresolved = [
        row for row in rows
        if bool(row.get("release_blocking", True)) and not row.get("resolved", False)
    ]
    return {
        "total": len(rows),
        "unresolved_release_blocking": len(unresolved),
        "rows": rows,
    }


def build_release_validation_packet(
    *,
    release_scope: Mapping[str, Any],
    config_hash: Optional[str] = None,
    source_snapshots: Mapping[str, Any] | None = None,
    benchmark_metrics: Iterable[Mapping[str, Any]] | None = None,
    serious_discordances: Iterable[Mapping[str, Any]] | None = None,
    sign_off_ledger: Iterable[Mapping[str, Any]] | None = None,
    validation_report_id: Optional[str] = None,
    analytical_report: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    """Build a JSON-native validation packet for one scoped release."""
    analytical_report = analytical_report or {}
    packet = {
        "packet_type": "scoped_release_validation_packet",
        "schema_version": "1.0.0",
        "generated_utc": _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat(),
        "validation_report_id": (
            validation_report_id
            or analytical_report.get("validation_report_id")
            or analytical_report.get("report_id")
        ),
        "release_scope": dict(release_scope),
        "config_hash": config_hash or analytical_report.get("config_hash"),
        "source_snapshots": dict(
            source_snapshots
            if source_snapshots is not None
            else source_snapshots_from_report(analytical_report)
        ),
        "benchmark_metrics": list(
            benchmark_metrics
            if benchmark_metrics is not None
            else benchmark_metrics_from_report(analytical_report, release_scope=release_scope)
        ),
        "serious_discordance_disposition": serious_discordance_disposition(
            serious_discordances or []
        ),
        "sign_off_ledger": [dict(row) for row in sign_off_ledger or []],
    }
    packet["packet_id"] = f"release-packet-{packet_digest(packet)[:16]}"
    return packet
