"""Application entry point. Builds the FastAPI app and exposes /health, /me, /onboarding."""

from fastapi import FastAPI

from api.me import router as me_router
from api.middleware import app_error_handler
from api.onboarding import router as onboarding_router
from core.exceptions import AppError
from core.lifespan import lifespan

app = FastAPI(title="Lead Intelligence Engine", version="0.1.0", lifespan=lifespan)
app.add_exception_handler(AppError, app_error_handler)
app.include_router(me_router)
app.include_router(onboarding_router)


@app.get("/health")
async def health() -> dict[str, str]:
    """Health check endpoint used by Docker, CI, and load balancers."""
    return {"status": "ok"}
