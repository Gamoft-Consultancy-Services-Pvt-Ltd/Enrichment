"""Application entry point. Builds the FastAPI app and exposes /health, /me, /onboarding."""

from fastapi import FastAPI

from api.lead_ingestion import router as lead_ingestion_router
from api.me import router as me_router
from api.middleware import app_error_handler
from api.onboarding import router as onboarding_router
from core.config import get_settings
from core.exceptions import AppError
from core.lifespan import lifespan

_settings = get_settings()

app = FastAPI(
    title="Lead Intelligence Engine",
    version="0.1.0",
    lifespan=lifespan,
    # Lets the /docs "Authorize" button log in via Auth0 (PKCE) and capture the token.
    swagger_ui_init_oauth={
        "clientId": _settings.auth0_spa_client_id,
        "usePkceWithAuthorizationCodeGrant": True,
        "scopes": "openid profile email",
        "additionalQueryStringParams": {"audience": _settings.auth0_audience},
    },
)
app.add_exception_handler(AppError, app_error_handler)
app.include_router(me_router)
app.include_router(onboarding_router)
app.include_router(lead_ingestion_router, prefix="/channels")


@app.get("/health")
async def health() -> dict[str, str]:
    """Health check endpoint used by Docker, CI, and load balancers."""
    return {"status": "ok"}
