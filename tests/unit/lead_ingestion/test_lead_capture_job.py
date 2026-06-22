"""Unit tests for run_lead_capture ARQ job — no DB, no network."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from workers.jobs.lead_ingestion import run_lead_capture


def _make_ctx() -> dict[str, object]:
    session = AsyncMock()
    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=session)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)
    return {"session_factory": factory}


def _status_update_payload() -> dict[str, Any]:
    return {
        "object": "whatsapp_business_account",
        "entry": [{"id": "123456789", "changes": [{"value": {
            "messaging_product": "whatsapp",
            "metadata": {"display_phone_number": "919876543210", "phone_number_id": "987654321"},
            "statuses": [{
                "id": "wamid.statusupdate001",
                "status": "read",
                "timestamp": "1700001500",
                "recipient_id": "919876543210",
            }],
        }, "field": "messages"}]}],
    }


async def test_run_lead_capture_skips_wa_status_update() -> None:
    """Status-update payloads (statuses key, no messages key) must not crash the worker."""
    ctx = _make_ctx()
    payload_dict: dict[str, Any] = {
        "tenant_id": str(uuid4()),
        "channel_connection_id": str(uuid4()),
        "raw_payload": _status_update_payload(),
    }

    with patch("workers.jobs.lead_ingestion.normalise_whatsapp_message") as mock_normalise:
        await run_lead_capture(ctx, payload_dict)

    mock_normalise.assert_not_called()


async def test_run_lead_capture_skips_wa_delivery_receipt() -> None:
    """Delivery confirmation webhooks (status=delivered, no messages key) are also skipped."""
    ctx = _make_ctx()
    payload_dict: dict[str, Any] = {
        "tenant_id": str(uuid4()),
        "channel_connection_id": str(uuid4()),
        "raw_payload": {
            "object": "whatsapp_business_account",
            "entry": [{"id": "123456789", "changes": [{"value": {
                "messaging_product": "whatsapp",
                "metadata": {"display_phone_number": "919876543210", "phone_number_id": "987654321"},
                "statuses": [{
                    "id": "wamid.deliveryreceipt001",
                    "status": "delivered",
                    "timestamp": "1700001600",
                    "recipient_id": "919876543210",
                }],
            }, "field": "messages"}]}],
        },
    }

    with patch("workers.jobs.lead_ingestion.normalise_whatsapp_message") as mock_normalise:
        await run_lead_capture(ctx, payload_dict)

    mock_normalise.assert_not_called()
