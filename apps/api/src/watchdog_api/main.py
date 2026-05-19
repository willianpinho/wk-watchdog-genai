"""FastAPI app factory + lifespan for wk-watchdog.

The factory pattern (vs a module-level `app = FastAPI(...)`) is
required so tests can inject a `Settings` with a temp DB path and
get a fully-wired app per test, without monkeypatching globals.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import aiosqlite
from fastapi import FastAPI

from watchdog_api import __version__
from watchdog_api.config import Settings, load_settings
from watchdog_api.routes.health import router as health_router
from watchdog_api.routes.ingestion import router as ingestion_router
from watchdog_core.persistence.migrations import apply_migrations


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup: ensure DB parent dir exists, apply migrations.

    The migration apply doubles as our writability check: if the path
    isn't writable, aiosqlite's first INSERT into schema_migrations
    fails and the app refuses to start.
    """
    settings: Settings = app.state.settings
    db_path = settings.db_path
    db_path.parent.mkdir(parents=True, exist_ok=True)

    async with aiosqlite.connect(str(db_path)) as conn:
        await apply_migrations(conn)
    yield
    # No SQLite shutdown actions required (connections are per-request).


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build a fresh FastAPI app wired to `settings`.

    Tests call this with a temp-DB Settings. Production callers pass
    no args; `load_settings()` resolves from the environment.
    """
    resolved = settings or load_settings()
    app = FastAPI(
        title="wk-watchdog API",
        version=__version__,
        lifespan=_lifespan,
    )
    app.state.settings = resolved
    app.include_router(health_router)
    app.include_router(ingestion_router)
    return app
