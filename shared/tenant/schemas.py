"""Public schemas and enums for the tenant module.

These are the single source of truth for tenant enums; api/, models.py, and
tests import them from here.
"""

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr


class BusinessType(StrEnum):
    """Whether the tenant sells to businesses or consumers."""

    B2B = "B2B"
    B2C = "B2C"


class TenantStatus(StrEnum):
    """The tenant lifecycle. 'Onboarding' is simply CREATED."""

    CREATED = "CREATED"
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    CHURNED = "CHURNED"


class TenantCreate(BaseModel):
    """Fields a caller provides to create a tenant. The system assigns the rest."""

    company_name: str
    primary_contact_name: str
    primary_contact_email: EmailStr
    business_type: BusinessType
    timezone: str = "UTC"
    language_preference: str = "en"


class TenantRead(BaseModel):
    """The full tenant record returned to callers; built from the ORM object."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    company_name: str
    primary_contact_name: str
    primary_contact_email: EmailStr
    business_type: BusinessType
    status: TenantStatus
    timezone: str
    language_preference: str
    created_at: datetime
    updated_at: datetime
    activated_at: datetime | None
