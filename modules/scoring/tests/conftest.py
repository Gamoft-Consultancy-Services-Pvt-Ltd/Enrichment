"""
Shared fixtures for the Epic 6 scoring integration tests (LEAD-61/62/63).

Everything runs against in-memory SQLite and the in-memory cache backend, so
the suite needs no Postgres or Redis — the same swappable-backend design used
in the repository (JSONBType) and cache (InMemoryCacheBackend) makes this work.

The engine / adapter / rating-client are lightweight fakes standing in for the
real LEAD-50/51/52 components, because the integration tests verify the *wiring*
(repository <-> cache <-> service <-> api/worker), not the scoring maths — that
is covered by the unit tests (LEAD-58/59/60).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core.db import Base
from db.cache import InMemoryCacheBackend, SignalSetCache
from db.repository import ScoringRepository
from services.service import ScoringService


# --------------------------------------------------------------------------- #
# Fakes for the injected scoring collaborators                                #
# --------------------------------------------------------------------------- #
@dataclass
class FakeResult:
    lead_id: str
    score: float
    classification: str
    confidence: float

    def model_dump(self) -> dict[str, Any]:
        return {
            "lead_id": self.lead_id,
            "score": self.score,
            "classification": self.classification,
            "confidence": self.confidence,
            "dimension_scores": {"fit": {"capped_points": 30}},
        }


class FakeAdapter:
    def to_weights(self, payload: dict[str, Any]) -> Any:
        return payload["weights"]


class FakeEngine:
    """L-HOT scores high-confidence; L-SPARSE is low-confidence until enriched."""

    def score(self, features: dict[str, Any], weights: Any) -> FakeResult:
        lead = features["lead_id"]
        if features.get("_enriched"):
            return FakeResult(lead, 78.0, "warm", 0.9)
        if lead == "L-SPARSE":
            return FakeResult(lead, 40.0, "cold", 0.3)
        return FakeResult(lead, 83.0, "hot", 0.95)


class FakeRatingClient:
    prompt_version = "signal-v3"
    model_version = "claude-sonnet-4-6"

    async def estimate_skipped(self, features: dict[str, Any], weights: Any) -> dict[str, Any]:
        return {**features, "_enriched": True}


# --------------------------------------------------------------------------- #
# Fixtures                                                                      #
# --------------------------------------------------------------------------- #
@pytest_asyncio.fixture
async def session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest_asyncio.fixture
async def seeded_factory(session_factory):
    """A factory whose DB already has an active SignalSet for 'venturedesk'."""
    async with session_factory() as s:
        await ScoringRepository(s).save_signal_set(
            tenant_id="venturedesk",
            version=1,
            payload={
                "weights": {"fit": 30},
                "version": 1,
                "source_pipeline": "onboarding-7",
                "signals": [{"name": "uses_crm"}, {"name": "headcount"}],
            },
        )
        await s.commit()
    return session_factory


@pytest.fixture
def cache():
    return SignalSetCache(InMemoryCacheBackend())


@pytest.fixture
def service(seeded_factory, cache):
    return ScoringService(
        session_factory=seeded_factory,
        cache=cache,
        adapter=FakeAdapter(),
        engine=FakeEngine(),
        rating_client=FakeRatingClient(),
        confidence_floor=0.5,
    )
