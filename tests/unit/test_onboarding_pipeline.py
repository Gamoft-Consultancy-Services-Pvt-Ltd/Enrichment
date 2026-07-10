"""Unit tests for modules/tenant_onboarding/pipeline — all I/O is mocked."""

from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest

from modules.tenant_onboarding.pipeline import CompanyInfo, run_pipeline
from shared.tenant.schemas import BusinessType, OnboardingStatus


def _mock_tenant(tenant_id: UUID | None = None) -> MagicMock:
    t = MagicMock()
    t.id = tenant_id or uuid4()
    t.company_name = "Acme"
    t.business_type = BusinessType.B2B
    t.website_url = "https://acme.com"
    return t


def _patch_common(monkeypatch: pytest.MonkeyPatch, tenant_id: UUID) -> AsyncMock:
    set_status_mock = AsyncMock()
    monkeypatch.setattr(
        "modules.tenant_onboarding.pipeline.get_tenant",
        AsyncMock(return_value=_mock_tenant(tenant_id)),
    )
    monkeypatch.setattr("modules.tenant_onboarding.pipeline.set_onboarding_status", set_status_mock)
    return set_status_mock


async def test_pipeline_happy_path_sets_complete(monkeypatch: pytest.MonkeyPatch) -> None:
    tenant_id = uuid4()
    mock_session = AsyncMock()
    set_status_mock = _patch_common(monkeypatch, tenant_id)

    from shared.tenant_config.schemas import Dimension, Signal, Thresholds, Weights

    company_info = CompanyInfo(summary="Acme makes CRM", industry="SaaS")
    business_profile = {"industry": "SaaS", "target_market": "SMB"}
    icp_data = {"buyer_role": "VP Sales"}
    sigs = [Signal(id=f"{d.value.lower()}_1", dimension=d, question="?") for d in Dimension]
    weights = Weights(fit=0.2, intent=0.2, engagement=0.2, behaviour=0.2, context=0.2)
    thresholds = Thresholds(hot=80, warm=55)

    persona_run = AsyncMock(return_value=business_profile)
    monkeypatch.setattr(
        "modules.tenant_onboarding.pipeline.research",
        AsyncMock(return_value=company_info),
    )
    monkeypatch.setattr("modules.tenant_onboarding.pipeline.persona.run", persona_run)
    monkeypatch.setattr(
        "modules.tenant_onboarding.pipeline.icp.run", AsyncMock(return_value=icp_data)
    )
    monkeypatch.setattr(
        "modules.tenant_onboarding.pipeline.signals.run",
        AsyncMock(return_value=(sigs, weights, thresholds)),
    )
    monkeypatch.setattr("modules.tenant_onboarding.pipeline.create_active", AsyncMock())
    monkeypatch.setattr("modules.tenant_onboarding.pipeline.activate_tenant", AsyncMock())

    await run_pipeline(mock_session, tenant_id)

    calls = [c.args[2] for c in set_status_mock.call_args_list]
    assert OnboardingStatus.RUNNING in calls
    assert OnboardingStatus.COMPLETE in calls
    # persona received the researched company_info as a dict
    assert persona_run.call_args.kwargs["company_info"] == company_info.model_dump()


async def test_pipeline_sets_failed_on_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    tenant_id = uuid4()
    mock_session = AsyncMock()
    set_status_mock = _patch_common(monkeypatch, tenant_id)

    monkeypatch.setattr(
        "modules.tenant_onboarding.pipeline.research",
        AsyncMock(side_effect=Exception("mcp down")),
    )

    with pytest.raises(Exception, match="mcp down"):
        await run_pipeline(mock_session, tenant_id)

    calls = [c.args[2] for c in set_status_mock.call_args_list]
    assert OnboardingStatus.RUNNING in calls
    assert OnboardingStatus.FAILED in calls
