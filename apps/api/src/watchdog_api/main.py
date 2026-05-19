"""FastAPI app factory + lifespan for wk-watchdog.

The factory pattern (vs a module-level `app = FastAPI(...)`) is
required so tests can inject a `Settings` with a temp DB path AND a
custom `SpanProcessor` (in-memory exporter) without monkeypatching
globals.

Lifespan responsibilities
-------------------------
1. Ensure the SQLite parent dir exists and apply migrations (the
   first INSERT into `schema_migrations` doubles as a writability
   check).
2. If `settings.webhook_target_url` is set, start the
   `OutboxWorker` as a background asyncio task and cancel it
   cleanly on shutdown.

Observability is wired at app-construction time (`create_app`), not
lifespan, because `FastAPIInstrumentor.instrument_app` must run before
the first request reaches the router stack.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

import aiosqlite
import structlog
from fastapi import FastAPI

from watchdog_api import __version__
from watchdog_api.config import Settings, load_settings
from watchdog_api.observability.logging import configure_logging
from watchdog_api.observability.otel import configure_otel
from watchdog_api.routes.health import router as health_router
from watchdog_api.routes.ingestion import router as ingestion_router
from watchdog_api.routes.metrics import router as metrics_router
from watchdog_api.routes.sink import router as sink_router
from watchdog_core.alerting.outbox_worker import OutboxWorker
from watchdog_core.alerting.webhook_dispatcher import WebhookDispatcher
from watchdog_core.persistence.migrations import apply_migrations

if TYPE_CHECKING:
    from opentelemetry.sdk.trace import SpanProcessor

logger = structlog.get_logger(__name__)


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    db_path = settings.db_path
    db_path.parent.mkdir(parents=True, exist_ok=True)

    async with aiosqlite.connect(str(db_path)) as conn:
        await apply_migrations(conn)

    worker_task: asyncio.Task[None] | None = None
    dispatcher: WebhookDispatcher | None = None
    if settings.webhook_target_url:
        dispatcher = WebhookDispatcher()
        worker = OutboxWorker(
            dispatcher=dispatcher,
            target_url=settings.webhook_target_url,
            secret=settings.webhook_secret,
            db_path=str(db_path),
            poll_interval_seconds=settings.worker_poll_interval_seconds,
        )
        worker_task = asyncio.create_task(worker.run(), name="outbox-worker")
        logger.info("outbox-worker-started", target=settings.webhook_target_url)

    try:
        yield
    finally:
        if worker_task is not None:
            worker_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await worker_task
        if dispatcher is not None:
            await dispatcher.aclose()


def create_app(
    settings: Settings | None = None,
    *,
    span_processor: SpanProcessor | None = None,
) -> FastAPI:
    """Build a fresh FastAPI app wired to `settings`.

    Tests pass `span_processor=SimpleSpanProcessor(InMemorySpanExporter(...))`
    to capture spans without hitting an OTLP endpoint.
    """
    resolved = settings or load_settings()
    configure_logging(env=resolved.env, log_level=resolved.log_level)

    app = FastAPI(
        title="wk-watchdog API",
        version=__version__,
        lifespan=_lifespan,
    )
    app.state.settings = resolved
    app.include_router(health_router)
    app.include_router(ingestion_router)
    app.include_router(sink_router)
    app.include_router(metrics_router)

    if resolved.otel_enabled:
        configure_otel(resolved, app, span_processor=span_processor)

    return app
