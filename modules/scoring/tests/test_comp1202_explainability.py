"""
COMP-1202-ST8 — Explainability test suite (Epic 6, Domain 12).

Covers the generator's determinism and the three reasoning layers (ST2/ST3/ST4),
plus the API and the tenant report (ST5/ST7).
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes_api import explanation_routes as exp_api
from db.repository import ScoringRepository
from schemas.explanation import ExplanationGenerator, ScoreExplanation

# generator tests are synchronous; API tests use TestClient (sync). No
# module-level asyncio mark needed.


def _hot_breakdown() -> dict:
    return {
        "lead_id": "L-HOT",
        "score": 83.0,
        "classification": "hot",
        "dimension_scores": {
            "Fit": {"capped_points": 30, "max_points": 30, "fired_signals": ["uses_crm"]},
            "Intent": {"capped_points": 20, "max_points": 30,
                       "fired_signals": ["pricing_visit"], "missed_signals": ["demo_request"]},
            "Engagement": {"capped_points": 33, "max_points": 40, "fired_signals": ["email_open"]},
        },
        "provenance": {"scorer": "deterministic_engine"},
    }


# --- generator determinism + reasoning layers ------------------------------ #
def test_generator_is_deterministic():
    gen = ExplanationGenerator()
    a = gen.explain(_hot_breakdown())
    b = gen.explain(_hot_breakdown())
    assert a == b  # same input -> identical explanation


def test_dimension_reasoning_mentions_fired_and_missed():
    exp = ExplanationGenerator().explain(_hot_breakdown())
    intent = next(d for d in exp.dimensions if d.dimension == "Intent")
    assert "pricing_visit" in intent.reasoning
    assert "demo_request" in intent.reasoning.lower()
    assert intent.points == 20 and intent.max_points == 30


def test_overall_reasoning_ranks_contributors_and_detractors():
    exp = ExplanationGenerator().explain(_hot_breakdown())
    # Fit (full 30) is a top contributor; Intent (gap of 10) is a top detractor
    assert "Fit" in exp.top_contributors
    assert "Intent" in exp.top_detractors


def test_bucket_reasoning_hot():
    exp = ExplanationGenerator().explain(_hot_breakdown())
    assert "HOT" in exp.bucket_reasoning
    assert exp.classification == "hot"


def test_bucket_reasoning_cold():
    bd = _hot_breakdown()
    bd.update(score=25.0, classification="cold")
    exp = ExplanationGenerator().explain(bd)
    assert "COLD" in exp.bucket_reasoning


def test_blocked_lead_explained_without_thresholds():
    bd = {"lead_id": "L-BLK", "score": 0.0, "classification": "blocked",
          "dimension_scores": {}}
    exp = ExplanationGenerator().explain(bd)
    assert exp.blocked is True
    assert "block" in exp.bucket_reasoning.lower()


def test_llm_assisted_surfaced_from_provenance():
    bd = _hot_breakdown()
    bd["provenance"] = {"scorer": "llm_fallback"}
    exp = ExplanationGenerator().explain(bd)
    assert exp.llm_assisted is True
    assert "LLM" in exp.summary or "fallback" in exp.summary.lower()


# --- API + report (over the real service stack) ---------------------------- #
@pytest.fixture
def explain_client(seeded_factory, service):
    import asyncio

    async def _seed():
        await service.score_lead(tenant_id="venturedesk", features={"lead_id": "L-HOT"})
        await service.score_lead(tenant_id="venturedesk", features={"lead_id": "L-SPARSE"})

    asyncio.get_event_loop().run_until_complete(_seed())

    svc = exp_api.ExplainabilityService(seeded_factory)
    app = FastAPI()
    app.include_router(exp_api.router)
    app.dependency_overrides[exp_api.get_explainability_service] = lambda: svc
    return TestClient(app)


def test_explanation_endpoint(explain_client):
    r = explain_client.get("/scoring/venturedesk/leads/L-HOT/explanation")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["classification"] == "hot"
    assert "bucket_reasoning" in body and body["bucket_reasoning"]
    assert "summary" in body and body["summary"]


def test_explanation_endpoint_404(explain_client):
    r = explain_client.get("/scoring/venturedesk/leads/NOPE/explanation")
    assert r.status_code == 404


def test_explanation_report(explain_client):
    r = explain_client.post(
        "/scoring/venturedesk/explanation-report",
        json={"lead_ids": ["L-HOT", "L-SPARSE", "NOPE"]},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total_leads"] == 2  # NOPE skipped
    assert body["llm_assisted_count"] >= 1  # L-SPARSE went through fallback
    assert sum(body["bucket_distribution"].values()) == 2
