"""Tests for core.db — async database engine and session wiring.

These run without a live database: SQLAlchemy connects lazily, so creating an
engine and a session object touches no network.
"""

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from sqlalchemy.orm import DeclarativeBase

from core.db import Base, engine, get_session


def test_base_is_declarative_base() -> None:
    """Base is the SQLAlchemy 2.0 declarative base that models will inherit."""
    assert issubclass(Base, DeclarativeBase)


def test_engine_is_async_with_configured_driver() -> None:
    """The shared engine is async and uses the asyncpg Postgres driver."""
    assert isinstance(engine, AsyncEngine)
    assert engine.url.drivername == "postgresql+asyncpg"


async def test_get_session_yields_async_session() -> None:
    """get_session yields an AsyncSession and then cleans it up."""
    gen = get_session()
    session = await gen.__anext__()
    try:
        assert isinstance(session, AsyncSession)
    finally:
        await gen.aclose()
