"""Unit tests for the User ORM model — structure only, no DB connection."""

from auth.models import User


def test_user_table_name() -> None:
    assert User.__tablename__ == "users"


def test_user_columns_and_constraints() -> None:
    cols = User.__table__.columns
    assert cols["auth0_sub"].unique is True
    assert cols["auth0_sub"].nullable is False
    assert cols["email"].nullable is False
    assert cols["role"].nullable is False
    # one user per tenant for now; admins have NULL tenant_id
    assert cols["tenant_id"].nullable is True
    assert cols["tenant_id"].unique is True
    assert any(fk.column.table.name == "tenants" for fk in cols["tenant_id"].foreign_keys)
