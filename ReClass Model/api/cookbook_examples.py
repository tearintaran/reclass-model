"""Executable cookbook client examples for the service API.

Each function accepts a TestClient/httpx-like ``client`` and request ``headers``.
The examples are intentionally plain Python dictionaries so they double as short
snippets for users and regression tests for the public API surface.
"""

from __future__ import annotations

from typing import Any, Dict

VARIANT: Dict[str, Any] = {"chrom": "1", "pos": 100, "ref": "A", "alt": "G"}


def _assert_ok(response, expected: int = 200) -> Dict[str, Any]:
    if response.status_code != expected:
        raise AssertionError(f"expected {expected}, got {response.status_code}: {response.text}")
    return response.json()


def evidence_resolution_flow(client, headers: Dict[str, str]) -> Dict[str, Any]:
    """Resolve provider evidence for a variant."""
    response = client.post(
        "/evidence/resolve",
        json={"variant": VARIANT, "providers": ["revel"]},
        headers=headers,
    )
    return _assert_ok(response)


def classify_flow(client, headers: Dict[str, str]) -> Dict[str, Any]:
    """Preview a deterministic classification without persisting it."""
    response = client.post(
        "/classify",
        json={
            "variant": VARIANT,
            "evidence": {"resolve": {"variant": VARIANT, "providers": ["revel"]}},
        },
        headers=headers,
    )
    return _assert_ok(response)


def sign_off_flow(client, headers: Dict[str, str]) -> Dict[str, Any]:
    """Persist a draft classification, then release it with reviewer sign-off."""
    draft = _assert_ok(
        client.post(
            "/classifications",
            json={
                "variant": VARIANT,
                "evidence": {
                    "events": [
                        {
                            "source": "curated",
                            "acmg_criterion": "PVS1",
                            "evidence_direction": "pathogenic",
                            "applied_strength": "very_strong",
                            "source_version": "cookbook-curation-v1",
                        }
                    ]
                },
            },
            headers=headers,
        ),
        expected=201,
    )
    classification_id = draft["receipt"]["classification_id"]
    return _assert_ok(
        client.post(
            f"/classifications/{classification_id}/sign-off",
            json={"signed_off_by": "Dr. Cookbook Reviewer, MD", "credential": "MD"},
            headers=headers,
        )
    )


def report_flow(client, headers: Dict[str, str]) -> Dict[str, Any]:
    """Fetch reviewer, patient-summary, and FHIR report surfaces."""
    signed = sign_off_flow(client, headers)
    classification_id = signed["classification_id"]
    reviewer = _assert_ok(
        client.get(f"/classifications/{classification_id}/report/reviewer", headers=headers)
    )
    summary = _assert_ok(
        client.get(f"/classifications/{classification_id}/report/summary", headers=headers)
    )
    fhir_bundle = _assert_ok(
        client.get(f"/classifications/{classification_id}/report/fhir", headers=headers)
    )
    return {"reviewer": reviewer, "summary": summary, "fhir": fhir_bundle}


def reanalysis_flow(client, headers: Dict[str, str]) -> Dict[str, Any]:
    """Run change-control reanalysis for a variant."""
    response = client.post(
        "/reanalysis/run",
        json={
            "variant": {"chrom": "1", "pos": 101, "ref": "A", "alt": "G"},
            "trigger": "provider_version",
            "evidence": {
                "events": [
                    {
                        "source": "curated",
                        "acmg_criterion": "PM2",
                        "evidence_direction": "pathogenic",
                        "applied_strength": "supporting",
                        "source_version": "cookbook-curation-v1",
                    }
                ]
            },
        },
        headers=headers,
    )
    return _assert_ok(response)


def alert_flow(client, headers: Dict[str, str]) -> Dict[str, Any]:
    """Create a tier-crossing alert and acknowledge it."""
    variant = {"chrom": "1", "pos": 102, "ref": "A", "alt": "G"}
    _assert_ok(
        client.post(
            "/reanalysis/run",
            json={
                "variant": variant,
                "evidence": {
                    "events": [
                        {
                            "source": "curated",
                            "acmg_criterion": "PM2",
                            "evidence_direction": "pathogenic",
                            "applied_strength": "supporting",
                        }
                    ]
                },
            },
            headers=headers,
        )
    )
    crossed = _assert_ok(
        client.post(
            "/reanalysis/run",
            json={
                "variant": variant,
                "trigger": "source_snapshot",
                "evidence": {
                    "events": [
                        {
                            "source": "curated",
                            "acmg_criterion": "PVS1",
                            "evidence_direction": "pathogenic",
                            "applied_strength": "very_strong",
                        },
                        {
                            "source": "curated",
                            "acmg_criterion": "PS1",
                            "evidence_direction": "pathogenic",
                            "applied_strength": "strong",
                        },
                    ]
                },
            },
            headers=headers,
        )
    )
    alert_id = crossed["result"]["alert_id"]
    return _assert_ok(
        client.post(
            f"/alerts/{alert_id}/state",
            json={"state": "acknowledged"},
            headers=headers,
        )
    )


def run_all(client, headers: Dict[str, str]) -> Dict[str, Any]:
    """Execute every cookbook flow against a live test app."""
    return {
        "evidence_resolution": evidence_resolution_flow(client, headers),
        "classify": classify_flow(client, headers),
        "sign_off": sign_off_flow(client, headers),
        "report": report_flow(client, headers),
        "reanalysis": reanalysis_flow(client, headers),
        "alert": alert_flow(client, headers),
    }
