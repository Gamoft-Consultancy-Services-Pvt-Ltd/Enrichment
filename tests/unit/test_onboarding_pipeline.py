"""Unit tests for modules/tenant_onboarding/pipeline — all I/O is mocked."""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import httpx
import pytest

from modules.tenant_onboarding.pipeline import (
    _combine_page_texts,
    _extract_domain,
    _strip_html,
    run_pipeline,
)
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
        "modules.tenant_onboarding.pipeline.search_site_pages",
        AsyncMock(return_value=[]),  # empty → triggers fallback to homepage
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
    mock_http_response.status_code = 200
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
        "modules.tenant_onboarding.pipeline.search_site_pages",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        "modules.tenant_onboarding.pipeline.persona.run",
        AsyncMock(side_effect=Exception("network timeout")),
    )

    with patch("modules.tenant_onboarding.pipeline.httpx.AsyncClient") as mock_http:
        mock_http_response = MagicMock()
        mock_http_response.status_code = 200
        mock_http_response.text = "<html>content</html>"
        mock_http_response.raise_for_status = MagicMock()
        mock_http.return_value.__aenter__.return_value.get = AsyncMock(
            return_value=mock_http_response
        )
        with pytest.raises(Exception, match="network timeout"):
            await run_pipeline(mock_session, tenant_id)

    calls = [c.args[2] for c in set_status_mock.call_args_list]
    assert OnboardingStatus.FAILED in calls


async def test_pipeline_uses_serpapi_pages_when_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant_id = uuid4()
    mock_session = AsyncMock()

    business_profile = {"industry": "SaaS", "target_market": "SMB"}
    icp_data = {"buyer_role": "VP Sales", "company_size": "50-200"}
    from shared.tenant_config.schemas import Dimension, Signal, Thresholds, Weights

    sigs = [Signal(id=f"{d.value.lower()}_1", dimension=d, question="?") for d in Dimension]
    weights = Weights(fit=0.2, intent=0.2, engagement=0.2, behaviour=0.2, context=0.2)
    thresholds = Thresholds(hot=80, warm=55)

    monkeypatch.setattr(
        "modules.tenant_onboarding.pipeline.get_tenant",
        AsyncMock(return_value=_mock_tenant(tenant_id)),
    )
    monkeypatch.setattr(
        "modules.tenant_onboarding.pipeline.set_onboarding_status", AsyncMock()
    )
    monkeypatch.setattr(
        "modules.tenant_onboarding.pipeline.search_site_pages",
        AsyncMock(return_value=["https://acme.com/about", "https://acme.com/products"]),
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

    mock_page = MagicMock()
    mock_page.status_code = 200
    mock_page.text = "<html><body>Acme page content</body></html>"

    with patch("modules.tenant_onboarding.pipeline.httpx.AsyncClient") as mock_http:
        mock_http.return_value.__aenter__.return_value.get = AsyncMock(
            return_value=mock_page
        )
        await run_pipeline(mock_session, tenant_id)


def test_extract_domain_strips_scheme_and_path() -> None:
    assert _extract_domain("https://acme.com/about") == "acme.com"


def test_extract_domain_handles_bare_domain() -> None:
    assert _extract_domain("https://acme.com") == "acme.com"


def test_combine_page_texts_joins_successful_responses() -> None:
    def make_resp(status: int, text: str) -> MagicMock:
        r = MagicMock(spec=httpx.Response)
        r.status_code = status
        r.text = text
        return r

    results: list[httpx.Response | BaseException] = [
        make_resp(200, "<p>About us</p>"),
        make_resp(200, "<p>Products</p>"),
        make_resp(404, "<p>Not found</p>"),
        ValueError("timeout"),
    ]
    combined = _combine_page_texts(results)
    assert "About us" in combined
    assert "Products" in combined
    assert "Not found" not in combined


def test_combine_page_texts_returns_empty_when_all_fail() -> None:
    results: list[httpx.Response | BaseException] = [
        ValueError("timeout"),
        RuntimeError("refused"),
    ]
    assert _combine_page_texts(results) == ""


async def test_pipeline_falls_back_to_homepage_when_all_page_fetches_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    # SerpAPI returns two URLs...
    monkeypatch.setattr(
        "modules.tenant_onboarding.pipeline.search_site_pages",
        AsyncMock(return_value=["https://acme.com/about", "https://acme.com/products"]),
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

    call_count = 0

    async def get_side_effect(url: str, **kwargs: object) -> MagicMock:
        nonlocal call_count
        call_count += 1
        if call_count <= 2:
            # First two calls (the gathered SerpAPI pages) raise an exception
            raise httpx.ConnectError("refused")
        # Third call is the fallback homepage fetch
        resp = MagicMock()
        resp.status_code = 200
        resp.text = "<html><body>Acme homepage</body></html>"
        resp.raise_for_status = MagicMock()
        return resp

    with patch("modules.tenant_onboarding.pipeline.httpx.AsyncClient") as mock_http:
        mock_http.return_value.__aenter__.return_value.get = get_side_effect
        await run_pipeline(mock_session, tenant_id)

    calls = [c.args[2] for c in set_status_mock.call_args_list]
    assert OnboardingStatus.RUNNING in calls
    assert OnboardingStatus.COMPLETE in calls
