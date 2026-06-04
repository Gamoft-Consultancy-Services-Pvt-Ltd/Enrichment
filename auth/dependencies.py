"""FastAPI auth dependencies: resolve the current User from the bearer token."""

from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from auth import token as token_module
from auth.models import User
from auth.schemas import Principal
from auth.service import get_or_create_user
from core.config import get_settings
from core.db import get_session
from core.exceptions import AuthenticationError

_bearer = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> User:
    """Validate the bearer token, then find-or-create and return the local User."""
    if credentials is None:
        raise AuthenticationError("Missing bearer token")
    claims = token_module.verify_token(credentials.credentials)
    principal = Principal.from_claims(claims, get_settings().auth_claim_namespace)
    return await get_or_create_user(session, principal)
