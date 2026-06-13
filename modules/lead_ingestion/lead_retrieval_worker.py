"""Lead Ad retrieval worker: fetch form data from Meta Graph API and capture lead.

On receiving a 'leadgen' webhook event:
  1. Extract the leadgen_id from the webhook payload.
  2. Fetch field_data from Meta Graph API: GET /{leadgen_id}?fields=field_data
     - On error code=100 (race condition): wait 3 seconds, retry once.
  3. Normalise the form data into a NormalisedChannelEvent via normalise_lead_ad().
  4. Bypass two_stage_filter entirely — Lead Ads are always genuine leads.
  5. Run pre_flight → run_capture.

Public surface:
  process_lead_ad_webhook(session, webhook_payload, *, tenant_id) -> (Lead, LeadReceived | None)

Internal helpers (patchable in tests):
  _fetch_lead_form_data(leadgen_id, *, access_token) — makes the Graph API HTTP call.
"""

import asyncio
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from modules.lead_ingestion.db.models import Lead
from modules.lead_ingestion.exceptions import ChannelApiError, PreFlightHaltError
from modules.lead_ingestion.normaliser import normalise_lead_ad
from modules.lead_ingestion.pipeline import create_terminal_lead, run_capture
from modules.lead_ingestion.pre_flight import check_pre_flight
from shared.events.schemas import LeadReceived, LeadSource

_META_GRAPH_BASE = "https://graph.facebook.com/v19.0"
_RACE_CONDITION_CODE = 100
_RETRY_DELAY_SECONDS = 3


async def _fetch_lead_form_data(
    leadgen_id: str,
    *,
    access_token: str = "",
) -> dict[str, Any]:
    """Fetch lead form field_data from the Meta Graph API.

    Retries once after 3 seconds if Meta returns error code=100 (race condition
    where the lead form data is not yet available immediately after the webhook).

    Args:
        leadgen_id: The leadgen ID from the webhook payload.
        access_token: Page access token for the Graph API request.

    Returns:
        The full lead form data dict (containing 'field_data' list).

    Raises:
        ChannelApiError: on non-retryable errors or if the retry also fails.
    """
    import httpx

    url = f"{_META_GRAPH_BASE}/{leadgen_id}"
    params: dict[str, str] = {"fields": "field_data"}
    if access_token:
        params["access_token"] = access_token

    async with httpx.AsyncClient() as client:
        resp = await client.get(url, params=params)

    data: dict[str, Any] = resp.json()

    # Meta sometimes returns error code=100 immediately after a leadgen webhook
    # because the form data hasn't propagated yet — retry once after 3 seconds.
    if not resp.is_success:
        error_code = data.get("error", {}).get("code")
        if error_code == _RACE_CONDITION_CODE:
            await asyncio.sleep(_RETRY_DELAY_SECONDS)
            async with httpx.AsyncClient() as client:
                resp = await client.get(url, params=params)
            data = resp.json()
            if not resp.is_success:
                raise ChannelApiError(
                    f"Meta Graph API failed after retry: {resp.status_code} {resp.text}"
                )
        else:
            raise ChannelApiError(
                f"Meta Graph API error {resp.status_code}: {data.get('error', resp.text)}"
            )

    return data


async def process_lead_ad_webhook(
    session: AsyncSession,
    webhook_payload: dict[str, Any],
    *,
    tenant_id: UUID,
    channel_connection_id: UUID | None = None,
    access_token: str = "",
) -> tuple[Lead, LeadReceived | None]:
    """Process one Facebook Lead Ad webhook event end-to-end.

    Lead Ads bypass two_stage_filter — form submissions are always genuine leads.

    Args:
        session: Active AsyncSession.
        webhook_payload: The raw webhook dict from Meta (contains 'entry' list).
        tenant_id: The tenant this connection belongs to.
        channel_connection_id: Optional ChannelConnection FK.
        access_token: Page access token for Graph API form data fetch.

    Returns:
        (Lead, LeadReceived) on the happy path, (Lead, None) for redeliveries.
    """
    # Extract leadgen_id from webhook payload
    change_value: dict[str, Any] = webhook_payload["entry"][0]["changes"][0]["value"]
    leadgen_id: str = change_value["leadgen_id"]

    # Determine the source: Instagram Lead Ads come from page-based events too,
    # but the webhook field distinguishes them. Default to FACEBOOK_LEAD_AD.
    source = LeadSource.FACEBOOK_LEAD_AD

    # Fetch form data from Meta Graph API (with race-condition retry)
    form_data = await _fetch_lead_form_data(leadgen_id, access_token=access_token)

    # Normalise into NormalisedChannelEvent — no LLM filter for Lead Ads
    event = normalise_lead_ad(
        leadgen_id,
        form_data,
        tenant_id=tenant_id,
        channel_connection_id=channel_connection_id,
        source=source,
    )

    # Pre-flight check (tenant must have an active config)
    try:
        await check_pre_flight(session, tenant_id)
    except PreFlightHaltError as exc:
        return await create_terminal_lead(
            session, event, "pre_flight_blocked", block_reason=str(exc)
        )

    # Capture — handles redelivery guard + dedup internally
    return await run_capture(session, event)
