"""ChannelConnection ORM model — shared so any module can read connection records.

Kept in shared/ because lead_ingestion reads connections on every webhook and
modules cannot import from each other.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, LargeBinary, String, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from core.db import Base


class ChannelConnection(Base):
    """A tenant's authenticated connection to one external channel."""

    __tablename__ = "channel_connections"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id"), nullable=False, index=True
    )
    channel_type: Mapped[str] = mapped_column(
        String, nullable=False
    )  # 'whatsapp' | 'instagram' | 'facebook' | 'google_sheets' | 'email'
    status: Mapped[str] = mapped_column(
        String, nullable=False, default="active"
    )  # 'active' | 'inactive' | 'expired'
    credentials_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Stored as "metadata" in Postgres; mapped_column attribute uses a non-reserved name
    # because Base.metadata is a SQLAlchemy reserved attribute on every ORM class.
    connection_metadata: Mapped[dict[str, Any] | None] = mapped_column(
        "metadata", JSONB, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
