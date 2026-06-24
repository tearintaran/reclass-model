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
from typing import Any, Dict, FrozenSet, Optional

from fastapi import HTTPException, status

from .settings import Settings


@dataclass(frozen=True)
class UserContext:
    """Authenticated caller scoped to one tenant."""

    user_id: str
    tenant_id: str
    roles: FrozenSet[str]
    display_name: str = ""

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
    return UserContext(
        user_id=str(user_id),
        tenant_id=str(tenant_id),
        roles=roles,
        display_name=str(payload.get("name") or payload.get("display_name") or user_id),
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


def authenticate_oidc(token: str, settings: Settings) -> UserContext:
    """Validate an RS256 bearer token against the configured JWKS (issuer/audience).

    Raises ``ValueError`` (not HTTP) so the caller can fall back to the HS256 dev path
    or API keys for non-OIDC tokens.
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
    return _user_from_jwt(payload)


def authenticate_bearer(token: str, settings: Settings) -> UserContext:
    """Validate a bearer token (asymmetric OIDC RS256, HS256 JWT, or API key).

    OIDC is tried first when configured; a non-OIDC token (HS256 dev token, API key)
    fails OIDC validation cleanly and falls through, so the development path stays
    intact behind ``RECLASS_API_ENV``.
    """
    token = token.strip()
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="empty bearer token",
        )
    if oidc_enabled(settings):
        try:
            return authenticate_oidc(token, settings)
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
