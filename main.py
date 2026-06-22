"""Application entry point. Builds the FastAPI app and exposes /health, /me, /onboarding."""

import pathlib

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

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


@app.get("/dev-tools/whatsapp-test", response_class=HTMLResponse, include_in_schema=False)
async def whatsapp_dev_test() -> HTMLResponse:
    """Serve the WhatsApp Embedded Signup dev test page (dev use only)."""
    html = (pathlib.Path(__file__).resolve().parent / "dev_tools" / "embedded_signup_test.html").read_text(encoding="utf-8")
    return HTMLResponse(content=html)
