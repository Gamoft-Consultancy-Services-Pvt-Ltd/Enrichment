"""Unit tests for the ingestion→enrichment enqueue helper."""

from unittest.mock import AsyncMock
from uuid import uuid4

from shared.events.schemas import LeadPayload, LeadReceived, LeadSource
from workers.jobs import lead_ingestion as li


def _received() -> LeadReceived:
    return LeadReceived(
        tenant_id=uuid4(),
        lead_id=uuid4(),
        source=LeadSource.EMAIL,
        payload=LeadPayload(name="Priya", source=LeadSource.EMAIL),
    )


async def test_dispatch_enqueues_pipeline_for_received_event() -> None:
    received = _received()
    pool = AsyncMock()
    ctx: dict[str, object] = {"redis": pool}

    await li._dispatch_enrichment(ctx, received)

    pool.enqueue_job.assert_awaited_once()
    call = pool.enqueue_job.await_args
    assert call is not None  # narrow _Call | None for mypy strict
    assert call.args[0] == "run_lead_pipeline"
    assert call.args[1] == received.model_dump(mode="json")
    assert call.kwargs["_job_id"] == f"enrich:{received.lead_id}"


async def test_dispatch_is_noop_when_no_event() -> None:
    pool = AsyncMock()
    ctx: dict[str, object] = {"redis": pool}

    await li._dispatch_enrichment(ctx, None)

    pool.enqueue_job.assert_not_awaited()
