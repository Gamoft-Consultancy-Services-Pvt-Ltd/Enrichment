"""Unit tests for modules/tenant_onboarding/pipeline — all I/O is mocked."""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest

from modules.tenant_onboarding.pipeline import _strip_html, run_pipeline
from shared.tenant.schemas import OnboardingStatus


def _mock_tenant(tenant_id: UUID | None = None) -> MagicMock:
    from shared.tenant.schemas import BusinessType

    t = MagicMock()
    t.id = tenant_id or uuid4()
    t.company_name = "Acme"
    t.business_type = BusinessType.B2B
    t.website_url = "https://acme.com"
    return t


def test_strip_html_removes_tags() -> None:
    html = "<html><body><h1>Hello</h1><script>bad()</script><p>World</p></body></html>"
    result = _strip_html(html)
    assert "Hello" in result
    assert "World" in result
    assert "<" not in result
    assert "bad()" not in result


async def test_pipeline_happy_path_sets_complete(monkeypatch: pytest.MonkeyPatch) -> None:
    tenant_id = uuid4()
    mock_session = AsyncMock()

    business_profile = {"industry": "SaaS", "target_market": "SMB"}
    icp_data = {"buyer_role": "VP Sales", "company_size": "50-200"}
    from shared.tenant_config.schemas import Dimension, Signal, Thresholds, Weights

    sigs = [Signal(id=f"{d.value.lower()}_1", dimension=d, question="?") for d in Dimension]
    weights = Weights(fit=0.2, intent=0.2, engagement=0.2, behaviour=0.2, context=0.2)
    thresholds = Thresholds(hot=80, warm=55)

    set_status_mock = AsyncMock()
    monkeypatch.setattr(
        "modules.tenant_onboarding.pipeline.get_tenant",
        AsyncMock(return_value=_mock_tenant(tenant_id)),
    )
    monkeypatch.setattr(
        "modules.tenant_onboarding.pipeline.set_onboarding_status", set_status_mock
    )
    monkeypatch.setattr(
        "modules.tenant_onboarding.pipeline.persona.run",
        AsyncMock(return_value=business_profile),
    )
    monkeypatch.setattr(
        "modules.tenant_onboarding.pipeline.icp.run", AsyncMock(return_value=icp_data)
    )
    monkeypatch.setattr(
        "modules.tenant_onboarding.pipeline.signals.run",
        AsyncMock(return_value=(sigs, weights, thresholds)),
    )
    monkeypatch.setattr("modules.tenant_onboarding.pipeline.create_active", AsyncMock())
    monkeypatch.setattr("modules.tenant_onboarding.pipeline.activate_tenant", AsyncMock())

    mock_http_response = MagicMock()
    mock_http_response.text = "<html><body>Acme sells CRM</body></html>"
    mock_http_response.raise_for_status = MagicMock()

    with patch("modules.tenant_onboarding.pipeline.httpx.AsyncClient") as mock_http:
        mock_http.return_value.__aenter__.return_value.get = AsyncMock(
            return_value=mock_http_response
        )
        await run_pipeline(mock_session, tenant_id)

    calls = [c.args[2] for c in set_status_mock.call_args_list]
    assert OnboardingStatus.RUNNING in calls
    assert OnboardingStatus.COMPLETE in calls


async def test_pipeline_sets_failed_on_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    tenant_id = uuid4()
    mock_session = AsyncMock()

    monkeypatch.setattr(
        "modules.tenant_onboarding.pipeline.get_tenant",
        AsyncMock(return_value=_mock_tenant(tenant_id)),
    )
    set_status_mock = AsyncMock()
    monkeypatch.setattr(
        "modules.tenant_onboarding.pipeline.set_onboarding_status", set_status_mock
    )
    monkeypatch.setattr(
        "modules.tenant_onboarding.pipeline.persona.run",
        AsyncMock(side_effect=Exception("network timeout")),
    )

    with patch("modules.tenant_onboarding.pipeline.httpx.AsyncClient") as mock_http:
        mock_http_response = MagicMock()
        mock_http_response.text = "<html>content</html>"
        mock_http_response.raise_for_status = MagicMock()
        mock_http.return_value.__aenter__.return_value.get = AsyncMock(
            return_value=mock_http_response
        )
        with pytest.raises(Exception, match="network timeout"):
            await run_pipeline(mock_session, tenant_id)

    calls = [c.args[2] for c in set_status_mock.call_args_list]
    assert OnboardingStatus.FAILED in calls
