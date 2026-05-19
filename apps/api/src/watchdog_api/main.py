"""FastAPI app factory + lifespan for wk-watchdog.

The factory pattern (vs a module-level `app = FastAPI(...)`) is
required so tests can inject a `Settings` with a temp DB path and
get a fully-wired app per test, without monkeypatching globals.

Lifespan responsibilities
-------------------------
1. Ensure the SQLite parent dir exists and apply migrations (the
   first INSERT into `schema_migrations` doubles as a writability
   check).
2. If `settings.webhook_target_url` is set, start the
   `OutboxWorker` as a background asyncio task and cancel it
   cleanly on shutdown. An empty `webhook_target_url` means we are
   running without delivery wired up (e.g., in a route-only test
   or a demo); the worker is then NOT started.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import aiosqlite
from fastapi import FastAPI

from watchdog_api import __version__
from watchdog_api.config import Settings, load_settings
from watchdog_api.routes.health import router as health_router
from watchdog_api.routes.ingestion import router as ingestion_router
from watchdog_api.routes.sink import router as sink_router
from watchdog_core.alerting.outbox_worker import OutboxWorker
from watchdog_core.alerting.webhook_dispatcher import WebhookDispatcher
from watchdog_core.persistence.migrations import apply_migrations

logger = logging.getLogger(__name__)


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
        logger.info("outbox worker started; target=%s", settings.webhook_target_url)

    try:
        yield
    finally:
        if worker_task is not None:
            worker_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await worker_task
        if dispatcher is not None:
            await dispatcher.aclose()


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build a fresh FastAPI app wired to `settings`."""
    resolved = settings or load_settings()
    app = FastAPI(
        title="wk-watchdog API",
        version=__version__,
        lifespan=_lifespan,
    )
    app.state.settings = resolved
    app.include_router(health_router)
    app.include_router(ingestion_router)
    app.include_router(sink_router)
    return app
