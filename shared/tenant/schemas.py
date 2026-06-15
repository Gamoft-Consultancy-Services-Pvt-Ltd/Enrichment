"""Public schemas and enums for the tenant module.

These are the single source of truth for tenant enums; api/, models.py, and
tests import them from here.
"""

import re
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, EmailStr, field_validator


class BusinessType(StrEnum):
    """Whether the tenant sells to businesses or consumers."""

    B2B = "B2B"
    B2C = "B2C"


class TenantStatus(StrEnum):
    """The tenant lifecycle. 'Onboarding' is simply CREATED."""

    CREATED   = "CREATED"
    ACTIVE    = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    CHURNED   = "CHURNED"


class OnboardingStatus(StrEnum):
    """Tracks where the tenant is in the automated onboarding pipeline.

    Lives here (not in modules/) because TenantRead imports it and shared/
    cannot import from modules/.
    """

    PENDING  = "PENDING"   # job queued, not yet picked up
    RUNNING  = "RUNNING"   # pipeline executing
    COMPLETE = "COMPLETE"  # tenant_config ACTIVE, tenant ACTIVE
    FAILED   = "FAILED"    # pipeline crashed; tenant can retry


class KybStatus(StrEnum):
    """Whether the tenant has proven control of its GSTIN via GST-OTP."""

    PENDING  = "PENDING"   # row created; OTP not yet confirmed
    VERIFIED = "VERIFIED"  # OTP confirmed; pipeline may run
    FAILED   = "FAILED"    # too many wrong attempts; tenant may restart


# Format check only; the state-code range is intentionally not constrained here
# (Surepass validates the GSTIN against the live GST authority during KYB).
GSTIN_PATTERN = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")


def normalize_gstin(value: str) -> str:
    """Strip, uppercase, and validate a GSTIN. Raise ValueError if malformed."""
    candidate = value.strip().upper()
    if not GSTIN_PATTERN.match(candidate):
        raise ValueError("invalid GSTIN format")
    return candidate


class TenantCreate(BaseModel):
    """Fields a caller provides to create a tenant. The system assigns the rest."""

    company_name: str
    primary_contact_name: str
    primary_contact_email: EmailStr
    business_type: BusinessType
    website_url: AnyHttpUrl
    gstin: str
    timezone: str = "UTC"
    language_preference: str = "en"

    @field_validator("gstin")
    @classmethod
    def _normalize_gstin(cls, value: str) -> str:
        return normalize_gstin(value)


class TenantRead(BaseModel):
    """The full tenant record returned to callers; built from the ORM object."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    company_name: str
    primary_contact_name: str
    primary_contact_email: EmailStr
    business_type: BusinessType
    gstin: str
    website_url: str
    onboarding_status: OnboardingStatus
    kyb_status: KybStatus
    status: TenantStatus
    timezone: str
    language_preference: str
    created_at: datetime
    updated_at: datetime
    activated_at: datetime | None
