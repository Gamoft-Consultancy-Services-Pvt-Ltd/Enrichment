"""HTTP error wiring: turn any AppError into a JSON response by its status_code."""

from fastapi import Request
from fastapi.responses import JSONResponse

from core.exceptions import AppError


async def app_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Map an AppError to a JSON response carrying its status_code and message."""
    assert isinstance(exc, AppError)
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})
