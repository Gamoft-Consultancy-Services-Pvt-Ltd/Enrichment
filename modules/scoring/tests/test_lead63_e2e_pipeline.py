"""
LEAD-63 — End-to-end scoring pipeline test (Epic 6).

Exercises the whole runtime as one flow, the way a lead actually travels:

    onboarding writes a SignalSet
        -> cache is cold, first score hits the DB once
        -> a HOT lead scores deterministically and is persisted
        -> a SPARSE lead trips the low-confidence Sonnet fallback and is rescored
        -> provenance (COMP-1201) is captured on the stored result
        -> onboarding regenerates the config (v2) and invalidates the cache
        -> the next score reflects v2 (no stale config)
        -> the second identical score for a tenant does NOT re-hit the DB (cache)

This is the story that proves repository + cache + service + persistence all
work together, not just in isolation.
"""

from __future__ import annotations

import pytest

from db.repository import ScoringRepository

pytestmark = pytest.mark.asyncio


async def test_full_pipeline_hot_lead(service, seeded_factory):
    outcome = await service.score_lead(
        tenant_id="venturedesk", features={"lead_id": "L-HOT"}
    )
    assert outcome.from_llm_fallback is False
    assert outcome.result.classification == "hot"

    # persisted and retrievable
    async with seeded_factory() as s:
        rec = await ScoringRepository(s).get_latest_result(
            tenant_id="venturedesk", lead_id="L-HOT"
        )
    assert rec is not None and rec.score == 83.0


async def test_full_pipeline_sparse_lead_uses_fallback_and_records_provenance(
    service, seeded_factory
):
    outcome = await service.score_lead(
        tenant_id="venturedesk", features={"lead_id": "L-SPARSE"}
    )
    assert outcome.from_llm_fallback is True
    assert outcome.result.classification == "warm"

    async with seeded_factory() as s:
        rec = await ScoringRepository(s).get_latest_result(
            tenant_id="venturedesk", lead_id="L-SPARSE"
        )
    prov = rec.breakdown["provenance"]
    assert prov["scorer"] == "llm_fallback"
    assert prov["model_version"] == "claude-sonnet-4-6"
    assert prov["prompt_version"] == "signal-v3"
    assert prov["signal_names"] == ["uses_crm", "headcount"]


async def test_config_regeneration_is_seen_after_invalidate(service, seeded_factory):
    # score once on v1
    await service.score_lead(tenant_id="venturedesk", features={"lead_id": "L-HOT"})

    # onboarding regenerates -> v2
    async with seeded_factory() as s:
        await ScoringRepository(s).save_signal_set(
            tenant_id="venturedesk",
            version=2,
            payload={"weights": {"fit": 35}, "version": 2, "signals": []},
        )
        await s.commit()
    await service.invalidate_signal_set("venturedesk")

    # next score must persist against v2
    outcome = await service.score_lead(
        tenant_id="venturedesk", features={"lead_id": "L-HOT2"}
    )
    async with seeded_factory() as s:
        rec = await ScoringRepository(s).get_latest_result(
            tenant_id="venturedesk", lead_id="L-HOT2"
        )
    assert rec.signal_set_version == 2


async def test_cache_prevents_repeated_db_reads(seeded_factory, cache):
    """Two scores for the same tenant should read the config from DB only once."""
    from conftest import FakeAdapter, FakeEngine
    from services.service import ScoringService

    db_reads = {"n": 0}

    # wrap the factory so we can count how often a session is opened for config
    real_factory = seeded_factory

    class CountingRepoService(ScoringService):
        async def _load_active_payload(self, tenant_id: str):
            async def loader():
                db_reads["n"] += 1
                async with real_factory() as s:
                    rec = await ScoringRepository(s).get_active_signal_set(
                        tenant_id=tenant_id
                    )
                    return rec.payload if rec else None

            return await self._cache.get_or_load(tenant_id, loader)

    svc = CountingRepoService(
        session_factory=real_factory,
        cache=cache,
        adapter=FakeAdapter(),
        engine=FakeEngine(),
    )

    await svc.score_lead(tenant_id="venturedesk", features={"lead_id": "L-1"})
    await svc.score_lead(tenant_id="venturedesk", features={"lead_id": "L-2"})

    assert db_reads["n"] == 1  # second score served config from cache
