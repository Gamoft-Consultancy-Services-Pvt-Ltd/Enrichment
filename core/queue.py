"""ARQ Redis pool — the application's background job queue.

The pool is created once in lifespan and stored on app.state.arq_pool.
Endpoints that enqueue jobs inject it via get_arq_pool().
"""

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings
from fastapi import Request

from core.config import get_settings


async def create_arq_pool() -> ArqRedis:
    """Create and return a connected ARQ Redis pool."""
    return await create_pool(RedisSettings.from_dsn(get_settings().redis_url))


def get_arq_pool(request: Request) -> ArqRedis:
    """FastAPI dependency: return the pool from app.state."""
    pool: ArqRedis = request.app.state.arq_pool
    return pool
