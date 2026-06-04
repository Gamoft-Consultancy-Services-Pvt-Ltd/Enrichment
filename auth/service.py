"""Public service for users — the only path to materialise a User from a Principal."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auth.models import User
from auth.schemas import Principal


async def get_or_create_user(session: AsyncSession, principal: Principal) -> User:
    """Return the User for this principal, creating it on first sight.

    Auth0 is the source of truth for email and role (always refreshed on login).
    tenant_id is DB-owned once set: a token that carries no tenant_id does not
    overwrite an existing link (it is assigned at onboarding, not by Auth0).
    """
    result = await session.execute(select(User).where(User.auth0_sub == principal.subject))
    user = result.scalar_one_or_none()
    if user is None:
        user = User(
            auth0_sub=principal.subject,
            email=principal.email,
            role=principal.role,
            tenant_id=principal.tenant_id,
        )
        session.add(user)
    else:
        user.email = principal.email
        user.role = principal.role
        # None means "no tenant claim in this token" — don't wipe a DB-owned link.
        if principal.tenant_id is not None:
            user.tenant_id = principal.tenant_id
    await session.commit()
    await session.refresh(user)
    return user


async def set_user_tenant(session: AsyncSession, user: User, tenant_id: UUID) -> User:
    """Link a user to a tenant and persist it.

    The caller is responsible for ensuring the tenant exists; a non-existent
    tenant_id surfaces as an IntegrityError at commit (FK violation).
    """
    user.tenant_id = tenant_id
    await session.commit()
    await session.refresh(user)
    return user
