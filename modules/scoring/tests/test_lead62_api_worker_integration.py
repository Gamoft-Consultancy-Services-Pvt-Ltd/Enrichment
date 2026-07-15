"""
LEAD-62 — API / worker integration tests (Epic 6).

Verifies the two entry points (LEAD-56 API, LEAD-57 worker) over the real
ScoringService + repository + cache stack:
- HTTP score endpoint returns the scored result and a 409 for unconfigured
  tenants.
- invalidate-config endpoint returns 204.
- The worker task scores via the same service and raises a permanent (no-retry)
  error for an unconfigured tenant.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes_api import routes
from worker.tasks import PermanentJobError, score_lead_task

# Only the worker tests are async; the TestClient (API) tests are synchronous,
# so the asyncio mark is applied per-test below rather than module-wide.


@pytest.fixture
def client(service):
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[routes.get_service] = lambda: service
    return TestClient(app)


def test_score_endpoint_returns_result(client):
    resp = client.post(
        "/scoring/venturedesk/score",
        json={"lead_id": "L-HOT", "features": {"region": "EU"}},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["classification"] == "hot"
    assert body["score"] == 83.0
    assert body["llm_adjusted"] is False
    assert body["record_id"] >= 1


def test_score_endpoint_triggers_llm_fallback(client):
    resp = client.post(
        "/scoring/venturedesk/score",
        json={"lead_id": "L-SPARSE", "features": {}},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # low-confidence lead -> Sonnet fallback ran -> rescored to warm
    assert body["llm_adjusted"] is True
    assert body["classification"] == "warm"


def test_score_endpoint_unconfigured_tenant_409(client):
    resp = client.post(
        "/scoring/ghost/score", json={"lead_id": "L-1", "features": {}}
    )
    assert resp.status_code == 409


def test_invalidate_config_endpoint_204(client):
    resp = client.post("/scoring/venturedesk/invalidate-config")
    assert resp.status_code == 204


@pytest.mark.asyncio
async def test_worker_scores_lead(service):
    ctx = {"scoring_service": service}
    result = await score_lead_task(
        ctx, tenant_id="venturedesk", lead_id="L-BATCH", features={"region": "US"}
    )
    assert result["classification"] == "hot"
    assert result["record_id"] >= 1


@pytest.mark.asyncio
async def test_worker_unconfigured_tenant_is_permanent(service):
    ctx = {"scoring_service": service}
    with pytest.raises(PermanentJobError):
        await score_lead_task(
            ctx, tenant_id="ghost", lead_id="L-1", features={}
        )
