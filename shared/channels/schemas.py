"""Public Pydantic schemas for ChannelConnection.

Other modules import ChannelConnectionRead for API responses; they never
import shared.channels.models directly.
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ChannelConnectionRead(BaseModel):
    """Read-only view of a channel connection, safe to expose in API responses."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    channel_type: str
    status: str
    expires_at: datetime | None
    created_at: datetime
    updated_at: datetime
