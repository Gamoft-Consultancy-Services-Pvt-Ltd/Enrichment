"""Unit tests for shared.tenant.schemas — pure validation, no DB."""

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from shared.tenant.schemas import (
    BusinessType,
    KybStatus,
    OnboardingStatus,
    TenantCreate,
    TenantRead,
    TenantStatus,
)

_VALID_GSTIN = "29ABCDE1234F1Z5"

_BASE = {
    "company_name": "Acme",
    "primary_contact_name": "Ada",
    "primary_contact_email": "ada@acme.com",
    "business_type": "B2B",
    "website_url": "https://acme.com",
}


def test_kyb_status_values() -> None:
    assert KybStatus.PENDING == "PENDING"
    assert KybStatus.VERIFIED == "VERIFIED"
    assert KybStatus.FAILED == "FAILED"


def test_tenant_create_accepts_valid_gstin() -> None:
    tc = TenantCreate(**_BASE, gstin=_VALID_GSTIN)
    assert tc.gstin == _VALID_GSTIN


def test_tenant_create_uppercases_and_strips_gstin() -> None:
    tc = TenantCreate(**_BASE, gstin=f"  {_VALID_GSTIN.lower()}  ")
    assert tc.gstin == _VALID_GSTIN


def test_tenant_create_rejects_malformed_gstin() -> None:
    with pytest.raises(ValidationError):
        TenantCreate(**_BASE, gstin="NOTAGSTIN")


def test_tenant_create_requires_gstin() -> None:
    with pytest.raises(ValidationError):
        TenantCreate(**_BASE)


def test_business_type_membership_is_exactly_b2b_and_b2c() -> None:
    assert {m.value for m in BusinessType} == {"B2B", "B2C"}


def test_tenant_status_membership_is_exact() -> None:
    assert {m.value for m in TenantStatus} == {
        "CREATED",
        "ACTIVE",
        "SUSPENDED",
        "CHURNED",
    }


def test_tenant_create_accepts_valid_input() -> None:
    data = TenantCreate(
        company_name="Gamoft",
        primary_contact_name="Asha",
        primary_contact_email="asha@gamoft.com",
        business_type=BusinessType.B2B,
        website_url="https://gamoft.com",  # type: ignore[arg-type]
        gstin="29ABCDE1234F1Z5",
    )
    assert data.business_type is BusinessType.B2B
    # timezone/language fall back to defaults
    assert data.timezone == "UTC"
    assert data.language_preference == "en"


def test_tenant_create_rejects_invalid_business_type() -> None:
    # model_validate takes Any, so an invalid value is a runtime (not type) error.
    with pytest.raises(ValidationError):
        TenantCreate.model_validate(
            {
                "company_name": "Gamoft",
                "primary_contact_name": "Asha",
                "primary_contact_email": "asha@gamoft.com",
                "business_type": "B2X",
            }
        )


def test_tenant_create_rejects_invalid_email() -> None:
    with pytest.raises(ValidationError):
        TenantCreate(
            company_name="Gamoft",
            primary_contact_name="Asha",
            primary_contact_email="not-an-email",
            business_type=BusinessType.B2B,
            website_url="https://gamoft.com",  # type: ignore[arg-type]
        )


def test_tenant_create_rejects_missing_required_field() -> None:
    with pytest.raises(ValidationError):
        TenantCreate.model_validate(
            {
                "company_name": "Gamoft",
                "primary_contact_name": "Asha",
                "business_type": "B2B",
            }
        )


def test_tenant_read_builds_from_orm_like_object() -> None:
    obj = SimpleNamespace(
        id=uuid4(),
        company_name="Gamoft",
        primary_contact_name="Asha",
        primary_contact_email="asha@gamoft.com",
        business_type=BusinessType.B2B,
        website_url="https://gamoft.com",
        onboarding_status=OnboardingStatus.PENDING,
        status=TenantStatus.CREATED,
        timezone="UTC",
        language_preference="en",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        activated_at=None,
    )
    read = TenantRead.model_validate(obj)
    assert read.company_name == "Gamoft"
    assert read.status is TenantStatus.CREATED
    assert read.onboarding_status is OnboardingStatus.PENDING
    assert read.activated_at is None
