"""The User ORM model (internal to auth).

Other code uses auth.service and auth.schemas, never this model directly. The
Role enum lives in schemas.py and is reused here.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Uuid, func
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.orm import Mapped, mapped_column

from auth.schemas import Role
from core.db import Base


class User(Base):
    """A human who authenticated via Auth0; keyed by the Auth0 subject claim."""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    auth0_sub: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    email: Mapped[str] = mapped_column(String, nullable=False)
    role: Mapped[Role] = mapped_column(SQLEnum(Role, name="user_role"), nullable=False)
    # Nullable + unique: NULL for platform_admins (Postgres allows many NULLs),
    # at most one user per tenant for now.
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("tenants.id"), nullable=True, unique=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
