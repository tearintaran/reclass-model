"""Role-based authorization for API endpoints."""

from __future__ import annotations

from typing import Callable, FrozenSet

from fastapi import Depends, HTTPException, status

from .auth import UserContext
from .deps import get_app_settings, get_current_user
from .settings import Settings

# Permission -> roles allowed to perform the action.
PERMISSIONS: dict[str, FrozenSet[str]] = {
    "classify:preview": frozenset({"viewer", "reviewer", "operator", "admin"}),
    "evidence:resolve": frozenset({"viewer", "reviewer", "operator", "admin"}),
    "classification:read": frozenset({"viewer", "reviewer", "operator", "admin"}),
    "classification:write": frozenset({"reviewer", "operator", "admin"}),
    "classification:sign_off": frozenset({"reviewer", "admin"}),
    # Worklist cases (product layer). PHI access is gated separately from the
    # de-identified queue: a plain viewer can work the queue but not see PHI.
    "case:read": frozenset({"viewer", "reviewer", "operator", "admin"}),
    "case:read_phi": frozenset({"reviewer", "operator", "admin"}),
    "case:write": frozenset({"reviewer", "operator", "admin"}),
    "case:transition": frozenset({"reviewer", "operator", "admin"}),
    "alert:read": frozenset({"viewer", "reviewer", "operator", "admin"}),
    "alert:write": frozenset({"reviewer", "operator", "admin"}),
    "reanalysis:run": frozenset({"operator", "admin"}),
    "audit:read": frozenset({"reviewer", "operator", "admin"}),
    "audit:write": frozenset({"operator", "admin"}),
    "tenant:admin": frozenset({"admin"}),
    "webhook:admin": frozenset({"operator", "admin"}),
    "webhook:emit": frozenset({"operator", "admin"}),
    "validation:run": frozenset({"admin"}),
}


def user_can(user: UserContext, permission: str) -> bool:
    allowed = PERMISSIONS.get(permission)
    if allowed is None:
        return False
    return bool(user.roles & allowed)


def require_permission(permission: str) -> Callable:
    """FastAPI dependency that enforces a permission on the current user."""

    def _check(user: UserContext = Depends(get_current_user)) -> UserContext:
        if not user_can(user, permission):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"permission denied: {permission}",
            )
        return user

    return _check


def is_platform_operator(user: UserContext, settings: Settings) -> bool:
    """True only for principals authorized for cross-tenant registry administration.

    A tenant ``admin`` role is necessary but **not sufficient** in production: roles
    are self-asserted in the token, so a tenant could otherwise claim ``admin`` and
    administer the whole platform registry (the C1/H1 chain). The deciding factor is a
    server-configured allowlist of platform subjects, optionally bound to the
    platform's own IdP. Development keeps the relaxed single-operator posture.
    """
    if not (user.roles & {"admin", "platform_admin"}):
        return False
    if settings.is_development:
        return True
    subjects = settings.platform_admin_subjects
    if not subjects or user.user_id not in subjects:
        return False
    if settings.platform_oidc_issuer and str(user.issuer) != str(settings.platform_oidc_issuer):
        return False
    return True


def require_platform_operator(
    user: UserContext = Depends(get_current_user),
    settings: Settings = Depends(get_app_settings),
) -> UserContext:
    """Enforce platform-operator authority (cross-tenant registry administration)."""
    if not is_platform_operator(user, settings):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="platform-operator authority required for cross-tenant administration",
        )
    return user
