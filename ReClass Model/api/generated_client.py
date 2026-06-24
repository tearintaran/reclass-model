"""Generated stdlib client for the pinned ReClass OpenAPI contract.

Regenerate with:
    python -c "from api.openapi_contract import generate_python_client; generate_python_client()"
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, Optional


OPERATIONS = {
    'list_tenants_admin_tenants_get': (
        'GET',
        '/admin/tenants',
    ),
    'create_tenant_admin_tenants_post': (
        'POST',
        '/admin/tenants',
    ),
    'get_tenant_admin_tenants__tenant_id__get': (
        'GET',
        '/admin/tenants/{tenant_id}',
    ),
    'update_tenant_admin_tenants__tenant_id__patch': (
        'PATCH',
        '/admin/tenants/{tenant_id}',
    ),
    'tenant_readiness_admin_tenants__tenant_id__readiness_get': (
        'GET',
        '/admin/tenants/{tenant_id}/readiness',
    ),
    'list_alerts_alerts_get': (
        'GET',
        '/alerts',
    ),
    'set_alert_state_alerts__alert_id__state_post': (
        'POST',
        '/alerts/{alert_id}/state',
    ),
    'set_alert_triage_alerts__alert_id__triage_post': (
        'POST',
        '/alerts/{alert_id}/triage',
    ),
    'list_audit_entries_audit_get': (
        'GET',
        '/audit',
    ),
    'audit_retention_policy_audit_retention_get': (
        'GET',
        '/audit/retention',
    ),
    'apply_audit_retention_audit_retention_apply_post': (
        'POST',
        '/audit/retention/apply',
    ),
    'create_security_event_audit_security_events_post': (
        'POST',
        '/audit/security-events',
    ),
    'list_classifications_classifications_get': (
        'GET',
        '/classifications',
    ),
    'create_classification_classifications_post': (
        'POST',
        '/classifications',
    ),
    'get_classification_classifications__classification_id__get': (
        'GET',
        '/classifications/{classification_id}',
    ),
    'fhir_report_classifications__classification_id__report_fhir_get': (
        'GET',
        '/classifications/{classification_id}/report/fhir',
    ),
    'amended_fhir_report_classifications__classification_id__report_fhir_amended_post': (
        'POST',
        '/classifications/{classification_id}/report/fhir/amended',
    ),
    'fhir_outbound_payload_classifications__classification_id__report_fhir_outbound_get': (
        'GET',
        '/classifications/{classification_id}/report/fhir/outbound',
    ),
    'reviewer_report_classifications__classification_id__report_reviewer_get': (
        'GET',
        '/classifications/{classification_id}/report/reviewer',
    ),
    'patient_summary_classifications__classification_id__report_summary_get': (
        'GET',
        '/classifications/{classification_id}/report/summary',
    ),
    'sign_off_classification_classifications__classification_id__sign_off_post': (
        'POST',
        '/classifications/{classification_id}/sign-off',
    ),
    'classify_variant_classify_post': (
        'POST',
        '/classify',
    ),
    'coverage_summary_evidence_coverage_get': (
        'GET',
        '/evidence/coverage',
    ),
    'record_coverage_evidence_coverage_post': (
        'POST',
        '/evidence/coverage',
    ),
    'list_curation_evidence_curation_get': (
        'GET',
        '/evidence/curation',
    ),
    'scan_curation_evidence_curation_scan_post': (
        'POST',
        '/evidence/curation/scan',
    ),
    'update_curation_state_evidence_curation__curation_id__state_post': (
        'POST',
        '/evidence/curation/{curation_id}/state',
    ),
    'import_batch_evidence_evidence_import_batch_post': (
        'POST',
        '/evidence/import/batch',
    ),
    'import_preview_evidence_import_preview_post': (
        'POST',
        '/evidence/import/preview',
    ),
    'list_providers_evidence_providers_get': (
        'GET',
        '/evidence/providers',
    ),
    'resolve_evidence_evidence_resolve_post': (
        'POST',
        '/evidence/resolve',
    ),
    'workbench_criteria_evidence_workbench_criteria_get': (
        'GET',
        '/evidence/workbench/criteria',
    ),
    'list_reviewer_evidence_evidence_workbench_evidence_get': (
        'GET',
        '/evidence/workbench/evidence',
    ),
    'submit_reviewer_evidence_evidence_workbench_evidence_post': (
        'POST',
        '/evidence/workbench/evidence',
    ),
    'update_reviewer_evidence_status_evidence_workbench_evidence__reviewer_evidence_id__status_post': (
        'POST',
        '/evidence/workbench/evidence/{reviewer_evidence_id}/status',
    ),
    'expire_reviewer_evidence_evidence_workbench_expire_post': (
        'POST',
        '/evidence/workbench/expire',
    ),
    'health_health_get': (
        'GET',
        '/health',
    ),
    'preflight_health_preflight_get': (
        'GET',
        '/health/preflight',
    ),
    'metrics_endpoint_metrics_get': (
        'GET',
        '/metrics',
    ),
    'reanalysis_operator_view_reanalysis_operator_view_get': (
        'GET',
        '/reanalysis/operator-view',
    ),
    'get_reanalysis_policy_reanalysis_policy_get': (
        'GET',
        '/reanalysis/policy',
    ),
    'set_reanalysis_policy_reanalysis_policy_post': (
        'POST',
        '/reanalysis/policy',
    ),
    'run_reanalysis_reanalysis_run_post': (
        'POST',
        '/reanalysis/run',
    ),
    'release_gate_preview_validation_release_gate_post': (
        'POST',
        '/validation/release-gate',
    ),
    'release_gate_status_validation_release_gate__classification_id__get': (
        'GET',
        '/validation/release-gate/{classification_id}',
    ),
    'approve_release_validation_release_gate__classification_id__approve_post': (
        'POST',
        '/validation/release-gate/{classification_id}/approve',
    ),
    'transition_release_state_endpoint_validation_release_gate__classification_id__state_post': (
        'POST',
        '/validation/release-gate/{classification_id}/state',
    ),
    'release_packet_preview_validation_release_packet_post': (
        'POST',
        '/validation/release-packet',
    ),
    'release_packet_for_classification_validation_release_packet__classification_id__get': (
        'GET',
        '/validation/release-packet/{classification_id}',
    ),
    'run_validation_validation_run_post': (
        'POST',
        '/validation/run',
    ),
    'list_webhook_deliveries_webhooks_deliveries_get': (
        'GET',
        '/webhooks/deliveries',
    ),
    'list_webhook_endpoints_webhooks_endpoints_get': (
        'GET',
        '/webhooks/endpoints',
    ),
    'register_webhook_endpoint_webhooks_endpoints_post': (
        'POST',
        '/webhooks/endpoints',
    ),
    'update_webhook_endpoint_webhooks_endpoints__endpoint_id__patch': (
        'PATCH',
        '/webhooks/endpoints/{endpoint_id}',
    ),
    'list_webhook_event_types_webhooks_event_types_get': (
        'GET',
        '/webhooks/event-types',
    ),
    'list_webhook_events_webhooks_events_get': (
        'GET',
        '/webhooks/events',
    ),
    'emit_webhook_event_webhooks_events_post': (
        'POST',
        '/webhooks/events',
    ),
    'list_cases_worklist_cases_get': (
        'GET',
        '/worklist/cases',
    ),
    'create_case_worklist_cases_post': (
        'POST',
        '/worklist/cases',
    ),
    'bulk_assign_cases_worklist_cases_bulk_assign_post': (
        'POST',
        '/worklist/cases/bulk/assign',
    ),
    'bulk_transition_cases_worklist_cases_bulk_transition_post': (
        'POST',
        '/worklist/cases/bulk/transition',
    ),
    'get_case_worklist_cases__case_id__get': (
        'GET',
        '/worklist/cases/{case_id}',
    ),
    'update_case_worklist_cases__case_id__patch': (
        'PATCH',
        '/worklist/cases/{case_id}',
    ),
    'attach_classification_worklist_cases__case_id__classifications_post': (
        'POST',
        '/worklist/cases/{case_id}/classifications',
    ),
    'transition_case_worklist_cases__case_id__transition_post': (
        'POST',
        '/worklist/cases/{case_id}/transition',
    ),
    'worklist_metrics_worklist_metrics_get': (
        'GET',
        '/worklist/metrics',
    ),
}


@dataclass(frozen=True)
class ApiResponse:
    status_code: int
    body: Any
    headers: Dict[str, str]


class ReClassClient:
    def __init__(self, base_url: str, *, bearer_token: Optional[str] = None,
                 tenant_id: Optional[str] = None, timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.bearer_token = bearer_token
        self.tenant_id = tenant_id
        self.timeout = timeout

    def request(self, method: str, path: str, *, json_body: Any = None,
                headers: Optional[Dict[str, str]] = None) -> ApiResponse:
        body = None if json_body is None else json.dumps(json_body).encode("utf-8")
        request_headers = {"Accept": "application/json"}
        if body is not None:
            request_headers["Content-Type"] = "application/json"
        if self.bearer_token:
            request_headers["Authorization"] = f"Bearer {self.bearer_token}"
        if self.tenant_id:
            request_headers["X-Tenant-Id"] = self.tenant_id
        request_headers.update(headers or {})
        req = urllib.request.Request(
            self.base_url + path,
            data=body,
            method=method,
            headers=request_headers,
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310
                raw = resp.read().decode("utf-8")
                payload = json.loads(raw) if raw else None
                return ApiResponse(resp.status, payload, dict(resp.headers))
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8")
            try:
                payload = json.loads(raw) if raw else None
            except json.JSONDecodeError:
                payload = raw
            return ApiResponse(exc.code, payload, dict(exc.headers))

    def call(self, operation_id: str, *, path_params: Optional[Dict[str, str]] = None,
             json_body: Any = None) -> ApiResponse:
        method, path = OPERATIONS[operation_id]
        for key, value in (path_params or {}).items():
            path = path.replace("{" + key + "}", str(value))
        return self.request(method, path, json_body=json_body)
