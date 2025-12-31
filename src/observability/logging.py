"""
Structured logging setup using structlog.
"""

import logging
import sys
from typing import Any

import structlog
from opentelemetry import trace

from src.config import get_settings


def add_trace_context(logger: Any, method_name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    """
    Add OpenTelemetry trace context to log records.

    Args:
        logger: The logger instance.
        method_name: The method name being called.
        event_dict: The event dictionary.

    Returns:
        The event dictionary with trace context added.
    """
    span = trace.get_current_span()
    if span and span.is_recording():
        ctx = span.get_span_context()
        event_dict["trace_id"] = format(ctx.trace_id, "032x")
        event_dict["span_id"] = format(ctx.span_id, "016x")
    return event_dict


def setup_logging() -> None:
    """
    Configure structured logging for the application.

    Sets up structlog with JSON or console output based on configuration.
    Integrates with standard library logging.
    """
    settings = get_settings()

    # Determine log level
    log_level = getattr(logging, settings.log_level.upper(), logging.INFO)

    # Shared processors for all loggers
    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        add_trace_context,  # Add trace_id and span_id to logs
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.stdlib.ExtraAdder(),
    ]

    # Configure output format
    if settings.log_format == "json":
        # JSON output for production
        renderer = structlog.processors.JSONRenderer()
    else:
        # Console output for development
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    # Configure structlog
    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Configure standard library logging
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.handlers = [handler]
    root_logger.setLevel(log_level)

    # Reduce noise from third-party libraries
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """
    Get a structured logger instance.

    Args:
        name: Logger name (usually __name__).

    Returns:
        BoundLogger: A structlog logger instance.
    """
    return structlog.get_logger(name)


def bind_context(**kwargs: Any) -> None:
    """
    Bind context variables to all subsequent log messages.

    Args:
        **kwargs: Key-value pairs to add to log context.
    """
    structlog.contextvars.bind_contextvars(**kwargs)


def clear_context() -> None:
    """Clear all bound context variables."""
    structlog.contextvars.clear_contextvars()
