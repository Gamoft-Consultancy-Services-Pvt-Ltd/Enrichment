"""Application entry point. Builds the FastAPI app and exposes /health, /me, /onboarding."""

import pathlib
import time
import uuid
from collections.abc import Awaitable, Callable

import structlog
from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import text

from api.lead_ingestion import router as lead_ingestion_router
from api.me import router as me_router
from api.middleware import app_error_handler
from api.onboarding import router as onboarding_router
from core.config import get_settings
from core.db import async_session_factory
from core.exceptions import AppError
from core.lifespan import lifespan

log = structlog.get_logger()

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


@app.middleware("http")
async def request_logging_middleware(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    request_id = str(uuid.uuid4())
    start = time.perf_counter()
    bound_log = log.bind(request_id=request_id, method=request.method, path=request.url.path)
    try:
        response = await call_next(request)
    except Exception:
        bound_log.exception("unhandled_error")
        raise
    duration_ms = round((time.perf_counter() - start) * 1000, 1)
    bound_log.info("request", status=response.status_code, duration_ms=duration_ms)
    response.headers["X-Request-Id"] = request_id
    return response


@app.get("/health")
async def health(request: Request) -> Response:
    """Deep health check: verifies DB and Redis connectivity before returning 200."""
    errors: list[str] = []

    try:
        async with async_session_factory() as session:
            await session.execute(text("SELECT 1"))
    except Exception as exc:
        # Log the detail server-side; never expose raw exception text to callers
        # (CodeQL py/stack-trace-exposure — it can carry internal connection info).
        log.warning("health check failed", component="db", error=str(exc))
        errors.append("db")

    try:
        pool = request.app.state.arq_pool
        await pool.ping()
    except Exception as exc:
        log.warning("health check failed", component="redis", error=str(exc))
        errors.append("redis")

    if errors:
        return JSONResponse({"status": "degraded", "errors": errors}, status_code=503)
    return JSONResponse({"status": "ok"})


@app.get("/dev-tools/whatsapp-test", response_class=HTMLResponse, include_in_schema=False)
async def whatsapp_dev_test() -> HTMLResponse:
    """Serve the WhatsApp Embedded Signup dev test page (dev use only)."""
    html = (
        pathlib.Path(__file__).resolve().parent / "dev_tools" / "embedded_signup_test.html"
    ).read_text(encoding="utf-8")
    return HTMLResponse(content=html)
