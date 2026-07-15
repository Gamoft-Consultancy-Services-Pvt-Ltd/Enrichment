"""Application exception hierarchy.

Every error the application raises on purpose derives from AppError. Each type
carries the HTTP status it maps to, so a single handler in api/ can turn any of
them into a response by reading exc.status_code.
"""

from typing import Any


class AppError(Exception):
    """Base class for all deliberate application errors."""

    status_code = 500

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class NotFoundError(AppError):
    """A requested entity does not exist."""

    status_code = 404


class ConflictError(AppError):
    """The request conflicts with current state (duplicate, bad transition)."""

    status_code = 409


class AuthenticationError(AppError):
    """Authentication failed (missing/invalid token or claims)."""

    status_code = 401


class ExternalServiceError(AppError):
    """An upstream external service (a clients/ wrapper) failed."""

    status_code = 502


class UnprocessableError(AppError):
    """The request is well-formed but cannot be processed (e.g. PAN not verified)."""

    status_code = 422


class ConfigurationError(AppError):
    """A required setting is missing or unusable — our deployment is wrong, not theirs."""

    status_code = 500
