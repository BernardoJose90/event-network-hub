"""TEST ONLY: fake identity from request headers.

#############################################################################
#  TEST ONLY — NOT AUTHENTICATION.                                          #
#  Any client can send any X-Role / X-User value. Replace this module with  #
#  real authentication (verified identity + group/role claims) before this  #
#  code is used anywhere other than a local test harness.                   #
#############################################################################
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Header, HTTPException, status

ROLE_VIEWER = "viewer"
ROLE_NETWORK_ADMIN = "network-admin"
KNOWN_ROLES = frozenset({ROLE_VIEWER, ROLE_NETWORK_ADMIN})

MAX_USER_LENGTH = 100


@dataclass(frozen=True)
class Principal:
    user: str
    role: str | None


def current_principal(
    x_user: Annotated[str | None, Header()] = None,
    x_role: Annotated[str | None, Header()] = None,
) -> Principal:
    """TEST ONLY: trust the client-supplied headers."""
    user = (x_user or "").strip()[:MAX_USER_LENGTH] or "anonymous"
    role = (x_role or "").strip().lower() or None
    return Principal(user=user, role=role)


def require_known_role(principal: Principal) -> None:
    """Fail closed: a missing or unknown role gets nothing."""
    if principal.role not in KNOWN_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"X-Role must be one of: {', '.join(sorted(KNOWN_ROLES))}",
        )


def require_admin(principal: Principal, action: str) -> None:
    if principal.role != ROLE_NETWORK_ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Only network admins can {action}",
        )
