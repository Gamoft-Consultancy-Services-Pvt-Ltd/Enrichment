"""Deterministic Shopify order-history fetch — NOT an LLM tool.

Called by the enrichment service when a tenant has Shopify credentials. The
credentials never reach the LLM. The RETURN VALUE is PII-free by design: order
facts only (id, timestamps, amounts, currency, line-item title/qty/price) — never
customer personal details — so no PII enters the enrichment result,
the scoring context, or any trace.

The real Shopify Admin API call is deferred; the shape below documents the intended
output for the scoring agent.
"""

from typing import Any

from modules.enrichment.schemas import ShopifyCreds


async def fetch_order_history(
    creds: ShopifyCreds,
    *,
    email: str | None = None,
    phone: str | None = None,
) -> dict[str, Any]:
    """Return PII-free order facts for a customer identified by email/phone.

    Intended output shape (order facts only, no customer PII):
        {
            "customer_found": bool,
            "order_count": int,
            "total_spent": float,
            "currency": str,
            "orders": [
                {"id": str, "created_at": str, "total": float,
                 "line_items": [{"title": str, "qty": int, "price": float}]},
            ],
        }

    The real Shopify Admin API integration is pending.
    """
    raise NotImplementedError("Shopify order-history integration pending")
