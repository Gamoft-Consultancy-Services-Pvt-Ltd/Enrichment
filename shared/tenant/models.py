"""The Tenant ORM model (internal to the tenant module).

Other modules never import this; they use shared.tenant.service and
shared.tenant.schemas. Enums are defined in schemas.py and reused here.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Integer, String, Uuid, func
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from core.db import Base
from shared.tenant.schemas import BusinessType, KybStatus, OnboardingStatus, TenantStatus


class Tenant(Base):
    """A business account / client workspace that owns all its data."""

    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_name: Mapped[str] = mapped_column(String, nullable=False)
    primary_contact_name: Mapped[str] = mapped_column(String, nullable=False)
    primary_contact_email: Mapped[str] = mapped_column(String, nullable=False)
    business_type: Mapped[BusinessType] = mapped_column(
        SQLEnum(BusinessType, name="business_type"), nullable=False
    )
    website_url: Mapped[str] = mapped_column(String, nullable=False)
    gstin: Mapped[str] = mapped_column(String, nullable=False)
    kyb_status: Mapped[KybStatus] = mapped_column(
        String, nullable=False, default=KybStatus.PENDING
    )
    kyb_company_data: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    kyb_txn_ref: Mapped[str | None] = mapped_column(String, nullable=True)
    kyb_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    kyb_resends: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    kyb_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    onboarding_status: Mapped[OnboardingStatus] = mapped_column(
        String, nullable=False, default=OnboardingStatus.PENDING
    )
    status: Mapped[TenantStatus] = mapped_column(
        SQLEnum(TenantStatus, name="tenant_status"),
        nullable=False,
        default=TenantStatus.CREATED,
    )
    timezone: Mapped[str] = mapped_column(String, nullable=False, default="UTC")
    language_preference: Mapped[str] = mapped_column(String, nullable=False, default="en")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
