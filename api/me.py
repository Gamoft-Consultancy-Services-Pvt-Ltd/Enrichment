"""The /me endpoint — returns the currently authenticated user."""

from typing import Annotated

from fastapi import APIRouter, Depends

from auth.dependencies import get_current_user
from auth.models import User
from auth.schemas import UserRead

router = APIRouter()


@router.get("/me", response_model=UserRead)
async def read_me(user: Annotated[User, Depends(get_current_user)]) -> User:
    """Return the authenticated user's record."""
    return user
