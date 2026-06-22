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
    """KYB verification state for a tenant. PAN flow only ever persists VERIFIED;
    PENDING is the column default and FAILED is reserved for future admin use."""

    PENDING  = "PENDING"
    VERIFIED = "VERIFIED"
    FAILED   = "FAILED"


PAN_PATTERN = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")
DOB_PATTERN = re.compile(r"^(0[1-9]|[12][0-9]|3[01])/(0[1-9]|1[0-2])/[0-9]{4}$")


def normalize_pan(value: str) -> str:
    """Strip, uppercase, and validate an Indian PAN. Raise ValueError if malformed."""
    candidate = value.strip().upper()
    if not PAN_PATTERN.match(candidate):
        raise ValueError("pan must match ^[A-Z]{5}[0-9]{4}[A-Z]$")
    return candidate


def validate_dob(value: str) -> str:
    """Validate a DD/MM/YYYY date (DOB for individuals, incorporation date for
    companies). Raise ValueError if malformed."""
    candidate = value.strip()
    if not DOB_PATTERN.match(candidate):
        raise ValueError("pan_dob must be DD/MM/YYYY")
    return candidate


class TenantCreate(BaseModel):
    """Fields a caller provides to create a tenant. The system assigns the rest."""

    company_name: str
    primary_contact_name: str
    primary_contact_email: EmailStr
    business_type: BusinessType
    website_url: AnyHttpUrl
    pan: str
    pan_holder_name: str
    pan_dob: str
    consent: bool
    timezone: str = "UTC"
    language_preference: str = "en"

    @field_validator("pan")
    @classmethod
    def _normalize_pan(cls, value: str) -> str:
        return normalize_pan(value)

    @field_validator("pan_holder_name")
    @classmethod
    def _check_holder_name(cls, value: str) -> str:
        stripped = value.strip()
        if len(stripped) < 2:
            raise ValueError("pan_holder_name must be at least 2 characters")
        return stripped

    @field_validator("pan_dob")
    @classmethod
    def _check_dob(cls, value: str) -> str:
        return validate_dob(value)

    @field_validator("consent")
    @classmethod
    def _require_consent(cls, value: bool) -> bool:
        # The tenant must explicitly consent to the PAN lookup; only True is accepted.
        if value is not True:
            raise ValueError("consent must be given (true) to verify a PAN")
        return value


class TenantRead(BaseModel):
    """The full tenant record returned to callers; built from the ORM object."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    company_name: str
    primary_contact_name: str
    primary_contact_email: EmailStr
    business_type: BusinessType
    website_url: str
    onboarding_status: OnboardingStatus
    status: TenantStatus
    timezone: str
    language_preference: str
    created_at: datetime
    updated_at: datetime
    activated_at: datetime | None
