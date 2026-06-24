"""Role-based authorization for API endpoints."""

from __future__ import annotations

from typing import Callable, FrozenSet

from fastapi import Depends, HTTPException, status

from .auth import UserContext
from .deps import get_current_user

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
