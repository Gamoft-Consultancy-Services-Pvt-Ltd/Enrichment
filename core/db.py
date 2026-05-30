"""Async database engine, session factory, and the ORM declarative base.

Models across shared/ and modules/ inherit from Base. Routes get a session via
Depends(get_session). The engine connects lazily, so importing this module does
not require a running database.
"""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from core.config import get_settings


class Base(DeclarativeBase):
    """Declarative base shared by every ORM model in the application."""


engine = create_async_engine(
    get_settings().database_url,
    pool_pre_ping=True,
)

async_session_factory = async_sessionmaker(
    engine,
    expire_on_commit=False,
)


async def get_session() -> AsyncGenerator[AsyncSession]:
    """Yield a database session for one unit of work, closing it afterwards."""
    async with async_session_factory() as session:
        yield session
