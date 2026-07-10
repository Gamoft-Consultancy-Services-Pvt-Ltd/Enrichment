"""Unit tests for the deterministic Shopify order-history fetch."""

import inspect

import pytest

from modules.enrichment.schemas import ShopifyCreds
from modules.enrichment.tools import shopify_order_history as soh


async def test_fetch_raises_not_implemented_until_integration_lands() -> None:
    creds = ShopifyCreds(shop_domain="acme.myshopify.com", access_token="tok")
    with pytest.raises(NotImplementedError):
        await soh.fetch_order_history(creds, email="a@b.com", phone=None)


def test_output_contract_is_pii_free_by_design() -> None:
    # Guardrail: the source must not reference customer PII fields in its output.
    src = inspect.getsource(soh)
    for banned in ("customer_email", "customer_phone", "customer_name", "address"):
        assert banned not in src
