"""Tests for core.exceptions — the application exception hierarchy."""

from core.exceptions import (
    AppError,
    ConflictError,
    ExternalServiceError,
    NotFoundError,
)


def test_app_error_defaults() -> None:
    """The base error carries its message, a 500 status, and empty details."""
    error = AppError("something broke")

    assert str(error) == "something broke"
    assert error.message == "something broke"
    assert error.status_code == 500
    assert error.details == {}


def test_app_error_keeps_details() -> None:
    """Structured details passed in are preserved for logging/responses."""
    error = AppError("bad thing", details={"lead_id": "l-1"})

    assert error.details == {"lead_id": "l-1"}


def test_not_found_error_is_404() -> None:
    """NotFoundError is an AppError with a 404 status."""
    error = NotFoundError("tenant t-999 not found")

    assert isinstance(error, AppError)
    assert error.status_code == 404


def test_conflict_error_is_409() -> None:
    """ConflictError is an AppError with a 409 status."""
    error = ConflictError("tenant already exists")

    assert isinstance(error, AppError)
    assert error.status_code == 409


def test_external_service_error_is_502() -> None:
    """ExternalServiceError is an AppError with a 502 status."""
    error = ExternalServiceError("surepass timed out")

    assert isinstance(error, AppError)
    assert error.status_code == 502
