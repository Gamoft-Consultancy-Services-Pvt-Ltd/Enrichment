"""Public auth schemas: the Role enum, the request Principal, and UserRead."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, ValidationError

from core.exceptions import AuthenticationError


class Role(StrEnum):
    """The two human roles, sourced from the Auth0 token's role claim."""

    PLATFORM_ADMIN = "PLATFORM_ADMIN"
    TENANT = "TENANT"


class Principal(BaseModel):
    """The authenticated identity, built from verified token claims; not persisted."""

    model_config = ConfigDict(frozen=True)

    subject: str
    email: EmailStr
    tenant_id: UUID | None
    role: Role

    @classmethod
    def from_claims(cls, claims: dict[str, Any], namespace: str) -> Principal:
        """Map a verified claims dict to a Principal, or raise AuthenticationError."""
        try:
            raw_tenant = claims.get(f"{namespace}tenant_id")
            return cls(
                subject=claims["sub"],
                # Auth0 only allows namespaced custom claims on the access token,
                # so email arrives as `{namespace}email`, not a bare `email`.
                email=claims[f"{namespace}email"],
                role=Role(claims[f"{namespace}role"]),
                tenant_id=UUID(raw_tenant) if raw_tenant else None,
            )
        except (KeyError, ValueError, ValidationError) as exc:
            raise AuthenticationError("Invalid or missing token claims") from exc


class UserRead(BaseModel):
    """The user record returned to callers (e.g. GET /me); built from the ORM object."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    auth0_sub: str
    email: EmailStr
    role: Role
    tenant_id: UUID | None
    created_at: datetime
    updated_at: datetime
