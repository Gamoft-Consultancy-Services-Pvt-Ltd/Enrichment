"""Public service for users — the only path to materialise a User from a Principal."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auth.models import User
from auth.schemas import Principal


async def get_or_create_user(session: AsyncSession, principal: Principal) -> User:
    """Return the User for this principal, creating it on first sight.

    Auth0 is the source of truth: an existing row's email/role/tenant_id are
    refreshed from the principal so changes in Auth0 propagate on next login.
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
        if principal.tenant_id is not None:
            user.tenant_id = principal.tenant_id
    await session.commit()
    await session.refresh(user)
    return user
