"""Unit tests for auth.schemas — pure validation, no DB."""

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from auth.schemas import Principal, Role, UserRead
from core.exceptions import AuthenticationError

NS = "https://leadengine/"


def test_role_membership_is_exact() -> None:
    assert {m.value for m in Role} == {"PLATFORM_ADMIN", "TENANT"}


def test_principal_from_claims_tenant_user() -> None:
    tenant_id = uuid4()
    claims = {
        "sub": "auth0|abc",
        "email": "user@acme.com",
        f"{NS}role": "TENANT",
        f"{NS}tenant_id": str(tenant_id),
    }
    p = Principal.from_claims(claims, NS)
    assert p.subject == "auth0|abc"
    assert p.email == "user@acme.com"
    assert p.role is Role.TENANT
    assert p.tenant_id == tenant_id


def test_principal_from_claims_admin_has_no_tenant() -> None:
    claims = {"sub": "auth0|admin", "email": "ops@us.com", f"{NS}role": "PLATFORM_ADMIN"}
    p = Principal.from_claims(claims, NS)
    assert p.role is Role.PLATFORM_ADMIN
    assert p.tenant_id is None


def test_principal_from_claims_missing_email_raises_auth_error() -> None:
    claims = {"sub": "auth0|abc", f"{NS}role": "TENANT"}
    with pytest.raises(AuthenticationError):
        Principal.from_claims(claims, NS)


def test_principal_from_claims_invalid_role_raises_auth_error() -> None:
    claims = {"sub": "auth0|abc", "email": "u@a.com", f"{NS}role": "WIZARD"}
    with pytest.raises(AuthenticationError):
        Principal.from_claims(claims, NS)


def test_user_read_builds_from_orm_like_object() -> None:
    obj = SimpleNamespace(
        id=uuid4(),
        auth0_sub="auth0|abc",
        email="user@acme.com",
        role=Role.TENANT,
        tenant_id=uuid4(),
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    read = UserRead.model_validate(obj)
    assert read.auth0_sub == "auth0|abc"
    assert read.role is Role.TENANT
