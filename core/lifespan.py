"""Application lifespan: startup and shutdown hooks for the FastAPI app.

FastAPI runs the code before `yield` once on startup and the code after it
once on shutdown. Wiring this into the app guarantees logging is configured
before any request is served.
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from core.logging import configure_logging, get_logger
from core.queue import create_arq_pool


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """Configure the app on startup, then hand control to the running server."""
    configure_logging()
    get_logger(__name__).info("application_startup")
    app.state.arq_pool = await create_arq_pool()
    yield
    await app.state.arq_pool.aclose()
    get_logger(__name__).info("application_shutdown")
