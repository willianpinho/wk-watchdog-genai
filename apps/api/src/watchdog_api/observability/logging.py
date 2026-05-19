"""structlog configuration with OTel trace-context binding.

Every log line in non-local environments is valid JSON. Logs emitted
inside an active OTel span carry `trace_id` and `span_id` so a log
aggregator (Loki, Datadog, etc.) can pivot from a noisy trace to the
exact log lines that participated.
"""

from __future__ import annotations

import logging
from typing import Any

import structlog
from opentelemetry import trace
from structlog.types import EventDict, WrappedLogger


def _add_otel_context(_: WrappedLogger, __: str, event_dict: EventDict) -> EventDict:
    """Structlog processor: bind trace_id + span_id from the current OTel span."""
    span = trace.get_current_span()
    ctx = span.get_span_context()
    if ctx.is_valid:
        event_dict["trace_id"] = format(ctx.trace_id, "032x")
        event_dict["span_id"] = format(ctx.span_id, "016x")
    return event_dict


def configure_logging(env: str, log_level: str = "INFO") -> None:
    """Install structlog as the default logger.

    `env == "local"` uses the developer-friendly ConsoleRenderer with
    colours and key=value layout. Anything else uses JSONRenderer so
    the output is machine-parseable in production.
    """
    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)
    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        timestamper,
        _add_otel_context,
    ]
    if env == "local":
        renderer: Any = structlog.dev.ConsoleRenderer(colors=True)
    else:
        renderer = structlog.processors.JSONRenderer()

    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, log_level.upper(), logging.INFO),
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
