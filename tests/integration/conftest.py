"""Fixtures for integration tests: a migrated Postgres and a clean session per test."""

import os
import subprocess
from collections.abc import AsyncGenerator

# Provide deterministic test-only values for secrets that the app validates at
# settings-load time. These protect nothing real — all integration test data is fake.
# Real values come from the environment (CI workflow env / local .env); setdefault
# only fires when the variable is absent, so it never overrides a real key.
os.environ.setdefault(
    "CHANNEL_CREDENTIALS_ENCRYPTION_KEY",
    # base64url(b"\x00" * 32) — 32 zero bytes, valid AES-256 key
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
)
os.environ.setdefault("META_APP_SECRET", "ci_test_meta_app_secret_00000000")

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Import every model module so Base.metadata is complete: the truncation below
# walks Base.metadata.sorted_tables, which resolves cross-table foreign keys.
import auth.models  # noqa: E402, F401
import modules.lead_ingestion.db.models  # noqa: E402, F401
import shared.channels.models  # noqa: E402, F401
import shared.tenant.models  # noqa: E402, F401
import shared.tenant_config.models  # noqa: E402, F401
from core.config import get_settings
from core.db import Base


@pytest.fixture(scope="session", autouse=True)
def _apply_migrations() -> None:
    """Bring the test database schema up to head once for the whole session."""
    subprocess.run(["uv", "run", "alembic", "upgrade", "head"], check=True)


@pytest.fixture
async def session() -> AsyncGenerator[AsyncSession]:
    """Yield an AsyncSession, then wipe all tables so the next test starts empty."""
    engine = create_async_engine(get_settings().database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as db_session:
            yield db_session
    finally:
        async with engine.begin() as conn:
            for table in reversed(Base.metadata.sorted_tables):
                await conn.execute(text(f'TRUNCATE TABLE "{table.name}" RESTART IDENTITY CASCADE'))
        await engine.dispose()
