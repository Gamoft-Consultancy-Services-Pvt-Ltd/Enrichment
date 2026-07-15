"""LEAD-47-S6 — ORM model tests (run against in-memory SQLite)."""
import asyncio
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from core.db import Base
from db.models import SignalSetRecord, ScoringResultRecord


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


@pytest.mark.asyncio
async def test_signal_set_record_persists_jsonb(session: AsyncSession):
    rec = SignalSetRecord(tenant_id="t-1", version=1, is_active=True,
                          payload={"weights": {"fit": 30}, "signals": []},
                          source_pipeline="onboarding-run-7")
    session.add(rec); await session.commit()
    got = (await session.execute(select(SignalSetRecord))).scalar_one()
    assert got.payload["weights"]["fit"] == 30
    assert got.source_pipeline == "onboarding-run-7"
    assert got.created_at is not None


@pytest.mark.asyncio
async def test_scoring_result_record_persists(session: AsyncSession):
    rec = ScoringResultRecord(lead_id="L-1", tenant_id="t-1", score=83.0,
                              classification="hot",
                              breakdown={"dimension_scores": {"fit": {}}})
    session.add(rec); await session.commit()
    got = (await session.execute(select(ScoringResultRecord))).scalar_one()
    assert got.score == 83.0 and got.classification == "hot"
    assert got.breakdown["dimension_scores"]["fit"] == {}


@pytest.mark.asyncio
async def test_only_one_active_signal_set_per_tenant(session: AsyncSession):
    session.add(SignalSetRecord(tenant_id="t-1", version=1, is_active=True, payload={}))
    await session.commit()
    # second active row for same tenant must violate the partial unique index
    session.add(SignalSetRecord(tenant_id="t-1", version=2, is_active=True, payload={}))
    with pytest.raises(IntegrityError):
        await session.commit()


@pytest.mark.asyncio
async def test_inactive_versions_coexist(session: AsyncSession):
    session.add(SignalSetRecord(tenant_id="t-1", version=1, is_active=False, payload={}))
    session.add(SignalSetRecord(tenant_id="t-1", version=2, is_active=False, payload={}))
    await session.commit()
    rows = (await session.execute(select(SignalSetRecord))).scalars().all()
    assert len(rows) == 2  # many inactive allowed


@pytest.mark.asyncio
async def test_active_allowed_across_different_tenants(session: AsyncSession):
    session.add(SignalSetRecord(tenant_id="t-1", version=1, is_active=True, payload={}))
    session.add(SignalSetRecord(tenant_id="t-2", version=1, is_active=True, payload={}))
    await session.commit()  # different tenants each active is fine
    rows = (await session.execute(select(SignalSetRecord))).scalars().all()
    assert len(rows) == 2


@pytest.mark.asyncio
async def test_unique_tenant_version(session: AsyncSession):
    session.add(SignalSetRecord(tenant_id="t-1", version=1, is_active=False, payload={}))
    await session.commit()
    session.add(SignalSetRecord(tenant_id="t-1", version=1, is_active=False, payload={}))
    with pytest.raises(IntegrityError):
        await session.commit()
