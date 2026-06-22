"""Meta Graph API client for Lead Ads — fetches field_data for a leadgen submission.

Public surface:
  fetch_lead_fields(leadgen_id, page_access_token, *, settings) -> list[dict]
"""

from typing import Any

from core.config import Settings
from core.exceptions import ExternalServiceError

_GRAPH_BASE = "https://graph.facebook.com"


async def fetch_lead_fields(
    leadgen_id: str,
    page_access_token: str,
    *,
    settings: Settings,
) -> list[dict[str, Any]]:
    """Fetch the field_data list for a Lead Ads form submission.

    Returns a list of {"name": "<field>", "values": ["<value>"]} dicts.

    Raises:
        ExternalServiceError: if the Graph API call fails.
    """
    import httpx

    version = settings.meta_graph_api_version
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{_GRAPH_BASE}/{version}/{leadgen_id}",
            params={"fields": "field_data", "access_token": page_access_token},
        )
    if not resp.is_success:
        raise ExternalServiceError(
            f"Failed to fetch lead fields for {leadgen_id}: {resp.status_code} {resp.text}"
        )
    data: dict[str, Any] = resp.json()
    result: list[dict[str, Any]] = data.get("field_data", [])
    return result
