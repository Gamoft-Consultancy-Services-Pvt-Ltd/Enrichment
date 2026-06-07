"""Alembic runtime, wired to the application's models.

Alembic runs synchronously (psycopg2); the app uses asyncpg at runtime, so we
rewrite the driver in the URL here. Importing shared.tenant.models registers the
Tenant table on Base.metadata so autogenerate can see it.
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

import auth.models  # noqa: F401  (register the users table on Base.metadata)
import shared.tenant.models  # noqa: F401  (register models on Base.metadata)
import shared.tenant_config.models  # noqa: F401  (register the tenant_configs table on Base.metadata)
from core.config import get_settings
from core.db import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# The app's DATABASE_URL uses asyncpg; Alembic needs a sync driver.
sync_url = get_settings().database_url.replace("+asyncpg", "+psycopg2")
config.set_main_option("sqlalchemy.url", sync_url)

target_metadata = Base.metadata


def run_migrations_online() -> None:
    """Run migrations against a live database connection."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section) or {},
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
    connectable.dispose()


run_migrations_online()
