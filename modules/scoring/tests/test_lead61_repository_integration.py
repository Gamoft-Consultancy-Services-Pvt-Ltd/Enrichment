"""
LEAD-61 — Repository / DB integration tests (Epic 6).

Verifies the persistence layer (LEAD-48) against a real database engine:
round-trips, the one-active-SignalSet-per-tenant rule, score history ordering,
and that provenance survives the JSONB round-trip (COMP-1201).
"""

from __future__ import annotations

import pytest

from db.repository import ScoringRepository

pytestmark = pytest.mark.asyncio


async def test_signal_set_round_trip(session_factory):
    async with session_factory() as s:
        repo = ScoringRepository(s)
        await repo.save_signal_set(
            tenant_id="t1", version=1, payload={"weights": {"fit": 30}}
        )
        await s.commit()
    async with session_factory() as s:
        cfg = await ScoringRepository(s).get_active_signal_set(tenant_id="t1")
    assert cfg is not None
    assert cfg.version == 1 and cfg.is_active is True
    assert cfg.payload["weights"]["fit"] == 30


async def test_activating_new_version_deactivates_old(session_factory):
    async with session_factory() as s:
        repo = ScoringRepository(s)
        await repo.save_signal_set(tenant_id="t1", version=1, payload={"weights": {}})
        await s.commit()
    async with session_factory() as s:
        repo = ScoringRepository(s)
        await repo.save_signal_set(tenant_id="t1", version=2, payload={"weights": {}})
        await s.commit()
    async with session_factory() as s:
        cfg = await ScoringRepository(s).get_active_signal_set(tenant_id="t1")
    # only one active config, and it is the newest
    assert cfg.version == 2


async def test_inactive_save_does_not_steal_active(session_factory):
    async with session_factory() as s:
        repo = ScoringRepository(s)
        await repo.save_signal_set(tenant_id="t1", version=1, payload={"weights": {}})
        await s.commit()
    async with session_factory() as s:
        repo = ScoringRepository(s)
        # store a draft without activating it
        await repo.save_signal_set(
            tenant_id="t1", version=2, payload={"weights": {}}, activate=False
        )
        await s.commit()
    async with session_factory() as s:
        cfg = await ScoringRepository(s).get_active_signal_set(tenant_id="t1")
    assert cfg.version == 1  # draft did not take over


async def test_save_and_fetch_scoring_result_with_provenance(session_factory):
    async with session_factory() as s:
        repo = ScoringRepository(s)
        await repo.save_scoring_result(
            lead_id="L-1",
            tenant_id="t1",
            signal_set_version=1,
            score=83.0,
            classification="hot",
            breakdown={"confidence": "high"},
            llm_adjusted=True,
            provenance={"prompt_version": "p1", "model_version": "claude-sonnet-4-6"},
        )
        await s.commit()
    async with session_factory() as s:
        rec = await ScoringRepository(s).get_latest_result(tenant_id="t1", lead_id="L-1")
    assert rec is not None and rec.score == 83.0 and rec.llm_adjusted is True
    # provenance survived the JSONB round-trip
    assert rec.breakdown["provenance"]["model_version"] == "claude-sonnet-4-6"


async def test_latest_result_picks_newest(session_factory):
    async with session_factory() as s:
        repo = ScoringRepository(s)
        for sc, cls in [(40.0, "cold"), (60.0, "warm"), (88.0, "hot")]:
            await repo.save_scoring_result(
                lead_id="L-1", tenant_id="t1", signal_set_version=1,
                score=sc, classification=cls, breakdown={},
            )
        await s.commit()
    async with session_factory() as s:
        rec = await ScoringRepository(s).get_latest_result(tenant_id="t1", lead_id="L-1")
        history = await ScoringRepository(s).list_results_for_lead(
            tenant_id="t1", lead_id="L-1"
        )
    assert rec.score == 88.0  # tie-break on id makes "latest" unambiguous
    assert [h.score for h in history] == [88.0, 60.0, 40.0]


async def test_get_latest_result_missing_returns_none(session_factory):
    async with session_factory() as s:
        rec = await ScoringRepository(s).get_latest_result(
            tenant_id="t1", lead_id="does-not-exist"
        )
    assert rec is None
