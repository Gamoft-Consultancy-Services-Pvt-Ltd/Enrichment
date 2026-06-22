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
        "onboarding_status",
        "status",
        "timezone",
        "language_preference",
        "created_at",
        "updated_at",
        "activated_at",
        "pan",
        "kyb_status",
        "kyb_company_data",
        "kyb_verified_at",
    }


def test_id_is_primary_key_and_activated_at_is_nullable() -> None:
    table = Tenant.__table__
    assert table.c.id.primary_key is True
    assert table.c.activated_at.nullable is True
    assert table.c.company_name.nullable is False


def test_tenant_model_has_pan_and_kyb_columns() -> None:
    from shared.tenant.models import Tenant

    cols = set(Tenant.__table__.columns.keys())
    assert {"pan", "kyb_status", "kyb_company_data", "kyb_verified_at"} <= cols
