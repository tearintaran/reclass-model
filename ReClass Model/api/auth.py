"""Authentication: identity extraction from Bearer JWT or API keys.

Production traffic must present ``Authorization: Bearer <token>``. Development
may fall back to ``X-Tenant-Id`` (legacy header-only mode) when no bearer token
is supplied, so local workflows and the existing test suite keep working.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, FrozenSet, Optional

from fastapi import HTTPException, status

from .settings import Settings

#: A per-tenant OIDC binding lookup: ``tenant_id -> {"oidc_issuer", "oidc_audience"}``
#: (or ``None`` when the tenant is not registered). Supplied by the request layer
#: from the tenant registry so the auth layer stays free of storage imports.
TenantBindingLookup = Callable[[str], Optional[Dict[str, Any]]]


@dataclass(frozen=True)
class UserContext:
    """Authenticated caller scoped to one tenant."""

    user_id: str
    tenant_id: str
    roles: FrozenSet[str]
    display_name: str = ""
    #: Verified token issuer (``iss``) for OIDC/JWT principals; ``None`` for API-key
    #: and legacy-dev sessions. Used to bind platform-operator authority to a
    #: trusted platform IdP rather than a tenant claim.
    issuer: Optional[str] = None

    def has_role(self, role: str) -> bool:
        return role in self.roles or "admin" in self.roles


def _b64url_decode(segment: str) -> bytes:
    pad = "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode(segment + pad)


def _decode_jwt(token: str, secret: str) -> Dict[str, Any]:
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("malformed JWT")
    header_b, payload_b, sig_b = parts
    signing_input = f"{header_b}.{payload_b}".encode()
    expected = hmac.new(secret.encode(), signing_input, hashlib.sha256).digest()
    if not hmac.compare_digest(_b64url_decode(sig_b), expected):
        raise ValueError("invalid JWT signature")
    payload = json.loads(_b64url_decode(payload_b))
    exp = payload.get("exp")
    if exp is not None and int(exp) < int(time.time()):
        raise ValueError("JWT expired")
    return payload


def _user_from_jwt(payload: Dict[str, Any]) -> UserContext:
    tenant_id = payload.get("tenant_id") or payload.get("tid")
    user_id = payload.get("sub") or payload.get("user_id")
    if not tenant_id or not user_id:
        raise ValueError("JWT missing tenant_id or sub")
    roles_raw = payload.get("roles") or payload.get("role") or ["viewer"]
    if isinstance(roles_raw, str):
        roles_raw = [roles_raw]
    roles = frozenset(str(r) for r in roles_raw)
    issuer = payload.get("iss")
    return UserContext(
        user_id=str(user_id),
        tenant_id=str(tenant_id),
        roles=roles,
        display_name=str(payload.get("name") or payload.get("display_name") or user_id),
        issuer=str(issuer) if issuer is not None else None,
    )


def _lookup_api_key(token: str, settings: Settings) -> Optional[UserContext]:
    entry = settings.api_keys.get(token)
    if entry is None:
        return None
    roles = entry.get("roles") or ["viewer"]
    if isinstance(roles, str):
        roles = [roles]
    return UserContext(
        user_id=str(entry.get("user_id") or entry.get("sub") or "api-key"),
        tenant_id=str(entry["tenant_id"]),
        roles=frozenset(str(r) for r in roles),
        display_name=str(entry.get("display_name") or "API key"),
    )


def oidc_enabled(settings: Settings) -> bool:
    """True when asymmetric (RS256/JWKS) validation is configured."""
    return bool(settings.oidc_issuer and (settings.oidc_jwks_url or settings.oidc_jwks))


def _audience_matches(claim: Any, expected: str) -> bool:
    if isinstance(claim, str):
        return claim == expected
    if isinstance(claim, (list, tuple)):
        return expected in [str(a) for a in claim]
    return False


def _enforce_tenant_oidc_binding(
    tenant_id: str, payload: Dict[str, Any], lookup: TenantBindingLookup
) -> None:
    """Reject a validly-signed OIDC token whose issuer/audience are not registered
    for the tenant it asserts.

    Without this, a single platform-wide IdP lets any validly-signed token set
    ``tenant_id`` to an arbitrary victim tenant and cross the PHI boundary (RLS then
    *authorizes* the access because the tenant claim was trusted). Fail closed: a
    tenant with no registered OIDC issuer cannot be asserted via a federated token.
    """
    try:
        record = lookup(tenant_id)
    except Exception:  # a registry error must never silently authorize  # noqa: BLE001
        record = None
    if not record or not record.get("oidc_issuer"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="tenant is not registered for federated (OIDC) authentication",
        )
    if str(payload.get("iss")) != str(record["oidc_issuer"]):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="token issuer is not bound to the asserted tenant",
        )
    expected_aud = record.get("oidc_audience")
    if expected_aud and not _audience_matches(payload.get("aud"), str(expected_aud)):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="token audience is not bound to the asserted tenant",
        )


def authenticate_oidc(
    token: str,
    settings: Settings,
    *,
    tenant_binding: Optional[TenantBindingLookup] = None,
) -> UserContext:
    """Validate an RS256 bearer token against the configured JWKS (issuer/audience).

    Raises ``ValueError`` (not HTTP) so the caller can fall back to the HS256 dev path
    or API keys for non-OIDC tokens. When ``tenant_binding`` is supplied, the verified
    token's issuer/audience must additionally match the asserted tenant's registered
    OIDC config, or a 403 is raised (cross-tenant impersonation guard).
    """
    from .oidc import decode_and_verify, jwks_client_for

    client = jwks_client_for(
        static_jwks=settings.oidc_jwks or None,
        url=settings.oidc_jwks_url or None,
    )
    if client is None:
        raise ValueError("OIDC not configured")
    payload = decode_and_verify(
        token, client,
        issuer=settings.oidc_issuer or None,
        audience=settings.oidc_audience or None,
    )
    user = _user_from_jwt(payload)
    if tenant_binding is not None:
        _enforce_tenant_oidc_binding(user.tenant_id, payload, tenant_binding)
    return user


def authenticate_bearer(
    token: str,
    settings: Settings,
    *,
    tenant_binding: Optional[TenantBindingLookup] = None,
) -> UserContext:
    """Validate a bearer token (asymmetric OIDC RS256, HS256 JWT, or API key).

    OIDC is tried first when configured; a non-OIDC token (HS256 dev token, API key)
    fails OIDC validation cleanly and falls through, so the development path stays
    intact behind ``RECLASS_API_ENV``. ``tenant_binding`` (when provided) is enforced
    only for OIDC-validated tokens; HS256/API-key callers are self-issued or static
    and are not subject to the federated tenant binding.
    """
    token = token.strip()
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="empty bearer token",
        )
    if oidc_enabled(settings):
        try:
            return authenticate_oidc(token, settings, tenant_binding=tenant_binding)
        except ValueError as exc:
            if settings.requires_oidc_auth:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail=f"invalid or expired OIDC bearer token: {exc}",
                ) from exc
            pass
    elif settings.requires_oidc_auth:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OIDC auth mode is required but OIDC/JWKS is not configured",
        )
    if settings.jwt_secret:
        try:
            return _user_from_jwt(_decode_jwt(token, settings.jwt_secret))
        except ValueError:
            pass
    user = _lookup_api_key(token, settings)
    if user is not None:
        return user
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="invalid or expired bearer token",
    )


def legacy_user_from_tenant(tenant_id: str, settings: Settings) -> UserContext:
    """Build a synthetic user for legacy header-only development access."""
    return UserContext(
        user_id="legacy-dev",
        tenant_id=tenant_id,
        roles=frozenset(settings.legacy_default_roles),
        display_name="Legacy dev session",
    )


def issue_jwt(
    *,
    user_id: str,
    tenant_id: str,
    roles: list[str],
    secret: str,
    ttl_seconds: int = 3600,
    display_name: str = "",
) -> str:
    """Mint an HS256 JWT for tests and local tooling."""
    header = base64.urlsafe_b64encode(
        json.dumps({"alg": "HS256", "typ": "JWT"}).encode()
    ).rstrip(b"=").decode()
    now = int(time.time())
    payload = {
        "sub": user_id,
        "tenant_id": tenant_id,
        "roles": roles,
        "name": display_name or user_id,
        "iat": now,
        "exp": now + ttl_seconds,
    }
    payload_b = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    signing_input = f"{header}.{payload_b}".encode()
    sig = base64.urlsafe_b64encode(
        hmac.new(secret.encode(), signing_input, hashlib.sha256).digest()
    ).rstrip(b"=").decode()
    return f"{header}.{payload_b}.{sig}"
