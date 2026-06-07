"""Unit test: AppError subclasses map to their status_code over HTTP."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.middleware import app_error_handler
from core.exceptions import AppError, AuthenticationError


def _app() -> FastAPI:
    app = FastAPI()
    app.add_exception_handler(AppError, app_error_handler)

    @app.get("/boom")
    async def boom() -> None:
        raise AuthenticationError("nope")

    return app


def test_app_error_handler_maps_status_and_message() -> None:
    client = TestClient(_app())
    resp = client.get("/boom")
    assert resp.status_code == 401
    assert resp.json() == {"detail": "nope"}
