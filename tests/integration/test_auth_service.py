"""Integration tests for auth.service.get_or_create_user — real Postgres."""

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from auth.models import User
from auth.schemas import Principal, Role
from auth.service import get_or_create_user, set_user_tenant
from shared.tenant.schemas import BusinessType, TenantCreate
from shared.tenant.service import create_tenant


def _admin_principal(sub: str) -> Principal:
    return Principal(subject=sub, email=f"{sub}@us.com", tenant_id=None, role=Role.PLATFORM_ADMIN)


async def test_creates_user_on_first_call_then_returns_same(session: AsyncSession) -> None:
    principal = _admin_principal("auth0|first")
    created = await get_or_create_user(session, principal)
    again = await get_or_create_user(session, principal)

    assert created.id == again.id
    rows = (await session.execute(select(User))).scalars().all()
    assert len(rows) == 1


async def test_admin_user_has_null_tenant_and_multiple_admins_allowed(
    session: AsyncSession,
) -> None:
    a = await get_or_create_user(session, _admin_principal("auth0|a"))
    b = await get_or_create_user(session, _admin_principal("auth0|b"))
    assert a.tenant_id is None
    assert b.tenant_id is None
    assert a.id != b.id


async def test_tenant_user_links_to_tenant(session: AsyncSession) -> None:
    tenant = await create_tenant(
        session,
        TenantCreate(
            company_name="Acme",
            primary_contact_name="Ada",
            primary_contact_email="ada@acme.com",
            business_type=BusinessType.B2B,
            website_url="https://acme.com",  # type: ignore[arg-type]
        ),
    )
    principal = Principal(
        subject="auth0|tenantuser", email="ada@acme.com", tenant_id=tenant.id, role=Role.TENANT
    )
    user = await get_or_create_user(session, principal)
    assert user.tenant_id == tenant.id
    assert user.role is Role.TENANT


async def test_second_user_for_same_tenant_violates_unique(session: AsyncSession) -> None:
    tenant = await create_tenant(
        session,
        TenantCreate(
            company_name="Acme",
            primary_contact_name="Ada",
            primary_contact_email="ada@acme.com",
            business_type=BusinessType.B2B,
            website_url="https://acme.com",  # type: ignore[arg-type]
        ),
    )
    await get_or_create_user(
        session,
        Principal(subject="auth0|u1", email="u1@acme.com", tenant_id=tenant.id, role=Role.TENANT),
    )
    with pytest.raises(IntegrityError):
        await get_or_create_user(
            session,
            Principal(
                subject="auth0|u2", email="u2@acme.com", tenant_id=tenant.id, role=Role.TENANT
            ),
        )


async def test_existing_tenant_link_is_preserved_when_token_lacks_tenant(
    session: AsyncSession,
) -> None:
    # User first appears already linked to a tenant (e.g. claim carried it once).
    tenant = await create_tenant(
        session,
        TenantCreate(
            company_name="Acme",
            primary_contact_name="Ada",
            primary_contact_email="ada@acme.com",
            business_type=BusinessType.B2B,
            website_url="https://acme.com",  # type: ignore[arg-type]
        ),
    )
    linked = Principal(
        subject="auth0|keep", email="ada@acme.com", tenant_id=tenant.id, role=Role.TENANT
    )
    await get_or_create_user(session, linked)

    # Next login: same user, but the token carries NO tenant_id.
    tokenless = Principal(
        subject="auth0|keep", email="ada@acme.com", tenant_id=None, role=Role.TENANT
    )
    user = await get_or_create_user(session, tokenless)

    assert user.tenant_id == tenant.id  # preserved, not wiped


async def test_set_user_tenant_links_and_persists(session: AsyncSession) -> None:
    user = await get_or_create_user(session, _admin_principal("auth0|link"))
    assert user.tenant_id is None

    tenant = await create_tenant(
        session,
        TenantCreate(
            company_name="Beta",
            primary_contact_name="Bo",
            primary_contact_email="bo@beta.com",
            business_type=BusinessType.B2C,
            website_url="https://beta.com",  # type: ignore[arg-type]
        ),
    )
    updated = await set_user_tenant(session, user, tenant.id)
    assert updated.tenant_id == tenant.id

    # Confirm it persisted by re-reading.
    fresh = (await session.execute(select(User).where(User.auth0_sub == "auth0|link"))).scalar_one()
    assert fresh.tenant_id == tenant.id
