"""Structured logging setup.

Configures structlog once at startup so every log line is structured
key-value data: JSON in production (for log collectors) and pretty,
coloured console output in development (for humans). Call configure_logging()
at startup, then get_logger(name) everywhere else.
"""

import logging
from typing import cast

import structlog

from core.config import Settings, get_settings


def configure_logging(settings: Settings | None = None) -> None:
    """Configure structlog for the whole process. Call once at startup."""
    settings = settings or get_settings()

    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if settings.env == "production":
        renderer: structlog.types.Processor = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer()

    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelNamesMapping()[settings.log_level]
        ),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Return a structured logger; pass __name__ to tag the source module."""
    return cast(structlog.stdlib.BoundLogger, structlog.get_logger(name))
