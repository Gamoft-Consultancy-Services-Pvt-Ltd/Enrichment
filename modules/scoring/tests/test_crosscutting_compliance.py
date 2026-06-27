"""
Cross-cutting compliance tests (Epic 6):
- COMP-303-ST5  scoring data deletion (right to erasure)
- COMP-801-ST4  AI activity logging
- COMP-1102-ST2/ST3  model & prompt version registration
"""

from __future__ import annotations

import pytest

from db.repository import ScoringRepository
from services.audit import build_scoring_audit_event
from services.model_registry import declare_scoring_ai_assets

# async DB/service tests are marked individually; pure-function tests are sync.


# --------------------------------------------------------------------------- #
# COMP-303-ST5 — scoring data deletion                                        #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_delete_removes_all_scores_for_lead(service, seeded_factory):
    # score the same lead twice -> two rows
    await service.score_lead(tenant_id="venturedesk", features={"lead_id": "L-ERASE"})
    await service.score_lead(tenant_id="venturedesk", features={"lead_id": "L-ERASE"})

    deleted = await service.delete_lead_scoring_data(
        tenant_id="venturedesk", lead_id="L-ERASE"
    )
    assert deleted == 2

    async with seeded_factory() as s:
        rec = await ScoringRepository(s).get_latest_result(
            tenant_id="venturedesk", lead_id="L-ERASE"
        )
    assert rec is None  # fully erased


@pytest.mark.asyncio
async def test_delete_is_scoped_to_lead_and_tenant(service, seeded_factory):
    await service.score_lead(tenant_id="venturedesk", features={"lead_id": "L-KEEP"})
    await service.score_lead(tenant_id="venturedesk", features={"lead_id": "L-GONE"})

    await service.delete_lead_scoring_data(tenant_id="venturedesk", lead_id="L-GONE")

    async with seeded_factory() as s:
        repo = ScoringRepository(s)
        kept = await repo.get_latest_result(tenant_id="venturedesk", lead_id="L-KEEP")
        gone = await repo.get_latest_result(tenant_id="venturedesk", lead_id="L-GONE")
    assert kept is not None  # untouched
    assert gone is None


@pytest.mark.asyncio
async def test_delete_missing_lead_returns_zero(service):
    deleted = await service.delete_lead_scoring_data(
        tenant_id="venturedesk", lead_id="never-scored"
    )
    assert deleted == 0


# --------------------------------------------------------------------------- #
# COMP-801-ST4 — AI activity logging                                          #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_score_emits_audit_event(seeded_factory, cache):
    from services.service import ScoringService
    from conftest import FakeAdapter, FakeEngine, FakeRatingClient

    captured: list[dict] = []

    class CapturingSink:
        async def emit(self, event):
            captured.append(event)

    svc = ScoringService(
        session_factory=seeded_factory,
        cache=cache,
        adapter=FakeAdapter(),
        engine=FakeEngine(),
        rating_client=FakeRatingClient(),
        confidence_floor=0.5,
        audit_sink=CapturingSink(),
    )
    await svc.score_lead(tenant_id="venturedesk", features={"lead_id": "L-SPARSE"})

    assert len(captured) == 1
    ev = captured[0]
    assert ev["event_type"] == "ai.activity"
    assert ev["activity"] == "lead_scored"
    assert ev["resource"]["id"] == "L-SPARSE"
    assert ev["decision"]["automated"] is True
    assert ev["decision"]["llm_assisted"] is True
    # LLM versions flow into the audit event from provenance
    assert ev["ai"]["model_version"] == "claude-sonnet-4-6"


@pytest.mark.asyncio
async def test_audit_sink_failure_does_not_break_scoring(seeded_factory, cache):
    from services.service import ScoringService
    from conftest import FakeAdapter, FakeEngine

    class BrokenSink:
        async def emit(self, event):
            raise RuntimeError("sink down")

    svc = ScoringService(
        session_factory=seeded_factory,
        cache=cache,
        adapter=FakeAdapter(),
        engine=FakeEngine(),
        audit_sink=BrokenSink(),
    )
    # scoring must still succeed despite the audit sink raising
    outcome = await svc.score_lead(tenant_id="venturedesk", features={"lead_id": "L-HOT"})
    assert outcome.record_id >= 1


def test_audit_event_excludes_personal_data():
    ev = build_scoring_audit_event(
        tenant_id="t1", lead_id="L-1", record_id=5, score=83.0,
        classification="hot", used_llm=False, model_version=None,
        prompt_version=None, signal_set_version=1,
    )
    # only identifiers + decision, no feature/PII payload
    assert "features" not in ev and "breakdown" not in ev
    assert ev["resource"]["score_record_id"] == 5
    assert ev["ai"]["scorer"] == "deterministic_engine"


# --------------------------------------------------------------------------- #
# COMP-1102-ST2/ST3 — model & prompt version registration                     #
# --------------------------------------------------------------------------- #
def test_declare_scoring_ai_assets():
    assets = declare_scoring_ai_assets(
        model_version="claude-sonnet-4-6", prompt_version="signal-v3"
    )
    payload = assets.to_registry_payload()
    assert payload["component"] == "scoring"
    # ST2: exactly one model (the fallback), with its version
    assert len(payload["models"]) == 1
    assert payload["models"][0]["version"] == "claude-sonnet-4-6"
    assert payload["models"][0]["provider"] == "anthropic"
    # ST3: exactly one prompt, with its version
    assert len(payload["prompts"]) == 1
    assert payload["prompts"][0]["version"] == "signal-v3"


def test_registry_versions_match_provenance_values():
    """The registered versions are the same ones the runtime stamps into
    provenance (ST4/ST5), so the registry and the per-score record agree."""
    assets = declare_scoring_ai_assets()
    model = assets.models[0].version
    prompt = assets.prompts[0].version
    # these defaults mirror what FakeRatingClient / the real client report
    assert model == "claude-sonnet-4-6"
    assert prompt == "signal-v3"
