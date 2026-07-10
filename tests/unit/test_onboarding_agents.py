"""Unit tests for the three onboarding agents — Groq client is mocked."""

from unittest.mock import AsyncMock, patch

from modules.tenant_onboarding.agents import icp, persona, signals
from shared.tenant.schemas import BusinessType
from shared.tenant_config.schemas import Dimension, Thresholds, Weights


async def test_persona_run_builds_profile_from_company_info() -> None:
    profile = {"industry": "SaaS", "target_market": "SMB", "products_services": "CRM",
               "company_size": "50", "geography": "India", "value_proposition": "fast"}
    with patch(
        "modules.tenant_onboarding.agents.persona.call_with_tool",
        AsyncMock(return_value=profile),
    ) as mock_call:
        result = await persona.run(
            company_name="Acme",
            business_type=BusinessType.B2B,
            company_info={"summary": "Acme makes CRM", "industry": "SaaS"},
        )
    assert result == profile
    prompt = mock_call.call_args.kwargs["prompt"]
    assert "Acme makes CRM" in prompt


async def test_icp_agent_returns_icp() -> None:
    business_profile = {"industry": "SaaS", "target_market": "SMB"}
    expected = {
        "buyer_role": "VP Sales",
        "company_size": "50-200",
        "industry_vertical": "Tech",
        "pain_points": "Manual tracking",
        "budget_range": "$10k-50k",
        "decision_timeline": "3 months",
    }
    with patch(
        "modules.tenant_onboarding.agents.icp.call_with_tool",
        new=AsyncMock(return_value=expected),
    ):
        result = await icp.run(business_profile)
    assert result == expected


async def test_signals_agent_returns_signals_weights_thresholds() -> None:
    raw = {
        "signals": [
            {"id": "fit_1", "dimension": "FIT", "question": "Right industry?"},
            {"id": "intent_1", "dimension": "INTENT", "question": "Visited pricing?"},
            {"id": "eng_1", "dimension": "ENGAGEMENT", "question": "Opened email?"},
            {"id": "beh_1", "dimension": "BEHAVIOUR", "question": "Requested demo?"},
            {"id": "ctx_1", "dimension": "CONTEXT", "question": "Raised funding?"},
        ],
        "weights": {
            "fit": 0.2,
            "intent": 0.2,
            "engagement": 0.2,
            "behaviour": 0.2,
            "context": 0.2,
        },
        "thresholds": {"hot": 80, "warm": 55},
    }
    with patch(
        "modules.tenant_onboarding.agents.signals.call_with_tool",
        new=AsyncMock(return_value=raw),
    ):
        sigs, weights, thresholds = await signals.run(
            business_profile={"industry": "SaaS"},
            icp_data={"buyer_role": "VP Sales"},
        )
    assert len(sigs) == 5
    assert all(s.dimension in Dimension for s in sigs)
    assert isinstance(weights, Weights)
    assert isinstance(thresholds, Thresholds)
    assert thresholds.hot == 80
