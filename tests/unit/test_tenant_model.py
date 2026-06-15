"""Metadata-level sanity tests for the Tenant model — no DB connection."""

from core.db import Base
from shared.tenant.models import Tenant


def test_tenant_table_is_registered_on_metadata() -> None:
    assert "tenants" in Base.metadata.tables


def test_tenant_has_the_expected_columns() -> None:
    columns = {column.name for column in Tenant.__table__.columns}
    assert columns == {
        "id",
        "company_name",
        "primary_contact_name",
        "primary_contact_email",
        "business_type",
        "website_url",
        "gstin",
        "kyb_status",
        "kyb_company_data",
        "kyb_txn_ref",
        "kyb_attempts",
        "kyb_resends",
        "kyb_verified_at",
        "onboarding_status",
        "status",
        "timezone",
        "language_preference",
        "created_at",
        "updated_at",
        "activated_at",
    }


def test_kyb_columns_have_expected_nullability() -> None:
    # Existence is already covered by test_tenant_has_the_expected_columns; here we
    # pin the nullability that the KYB gate depends on.
    cols = Tenant.__table__.c
    assert cols.gstin.nullable is False
    assert cols.kyb_status.nullable is False
    assert cols.kyb_attempts.nullable is False
    assert cols.kyb_resends.nullable is False
    assert cols.kyb_company_data.nullable is True
    assert cols.kyb_txn_ref.nullable is True
    assert cols.kyb_verified_at.nullable is True


def test_id_is_primary_key_and_activated_at_is_nullable() -> None:
    table = Tenant.__table__
    assert table.c.id.primary_key is True
    assert table.c.activated_at.nullable is True
    assert table.c.company_name.nullable is False
