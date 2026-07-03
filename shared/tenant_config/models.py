"""The TenantConfig ORM model (internal to the tenant_config module).

Other modules never import this; they use shared.tenant_config.service and
shared.tenant_config.schemas. The status enum is defined in schemas.py and
reused here. Two partial unique indexes enforce at most one ACTIVE and one
DRAFT version per tenant.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, UniqueConstraint, Uuid, func, text
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from core.db import Base
from shared.tenant_config.schemas import ConfigStatus


class TenantConfig(Base):
    """A versioned scoring-configuration record owned by one tenant."""

    __tablename__ = "tenant_configs"

    __table_args__ = (
        UniqueConstraint("tenant_id", "version", name="uq_tenant_config_version"),
        Index(
            "uq_active_config_per_tenant",
            "tenant_id",
            unique=True,
            postgresql_where=text("status = 'ACTIVE'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[ConfigStatus] = mapped_column(
        SQLEnum(ConfigStatus, name="config_status"), nullable=False
    )
    business_profile: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    icp: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    signals: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    weights: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    thresholds: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
