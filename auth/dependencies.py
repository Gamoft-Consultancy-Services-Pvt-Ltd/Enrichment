"""FastAPI auth dependencies: resolve the current User from the bearer token.

The bearer token is still validated exactly the same way (Auth0 JWT). We use
OAuth2AuthorizationCodeBearer instead of a plain HTTPBearer so the OpenAPI/Swagger
docs advertise Auth0's login flow — letting the /docs "Authorize" button perform
the login directly. It reads the same `Authorization: Bearer <token>` header.
"""

from typing import Annotated

from fastapi import Depends, HTTPException
from fastapi.security import OAuth2AuthorizationCodeBearer
from sqlalchemy.ext.asyncio import AsyncSession

from auth import token as token_module
from auth.models import User
from auth.schemas import Principal, Role
from auth.service import get_or_create_user
from core.config import get_settings
from core.db import get_session
from core.exceptions import AuthenticationError

_settings = get_settings()
_oauth2_scheme = OAuth2AuthorizationCodeBearer(
    authorizationUrl=f"https://{_settings.auth0_domain}/authorize",
    tokenUrl=f"https://{_settings.auth0_domain}/oauth/token",
    auto_error=False,
)


async def get_current_user(
    token: Annotated[str | None, Depends(_oauth2_scheme)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> User:
    """Validate the bearer token, then find-or-create and return the local User."""
    if token is None:
        raise AuthenticationError("Missing bearer token")
    claims = token_module.verify_token(token)
    principal = Principal.from_claims(claims, get_settings().auth_claim_namespace)
    return await get_or_create_user(session, principal)


async def require_tenant_user(
    user: Annotated[User, Depends(get_current_user)],
) -> User:
    """Require TENANT role and a linked tenant_id; raise 403 for platform_admins."""
    if user.role != Role.TENANT or user.tenant_id is None:
        raise HTTPException(status_code=403, detail="tenant_role_required")
    return user


async def require_platform_admin(
    user: Annotated[User, Depends(get_current_user)],
) -> User:
    """Require PLATFORM_ADMIN role; raise 403 for tenant users."""
    if user.role != Role.PLATFORM_ADMIN:
        raise HTTPException(status_code=403, detail="platform_admin_role_required")
    return user
