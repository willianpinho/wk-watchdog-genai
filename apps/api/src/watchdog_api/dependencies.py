"""FastAPI dependency-injection wiring for wk-watchdog.

Each dependency is a callable so tests can `app.dependency_overrides[...]`
to inject fakes without monkeypatching the modules they live in.

Performance note: `get_db` opens a fresh aiosqlite connection per
request. SQLite connect is sub-millisecond on a local file, which is
fine for the MVP. A real production deployment should swap in a pool
(e.g., an asyncio.Queue-backed connection pool, or aiosqlitepool when
its API stabilizes) when the connect cost dominates a request budget.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Annotated

import aiosqlite
from fastapi import Depends, Request

from watchdog_core.persistence.migrations import apply_pragmas
from watchdog_core.persistence.repositories import LogEventRepository
from watchdog_core.services.ingestion_service import IngestionService

if TYPE_CHECKING:
    from watchdog_api.config import Settings


async def get_db(request: Request) -> AsyncIterator[aiosqlite.Connection]:
    """Per-request aiosqlite connection with WAL + foreign_keys."""
    settings: Settings = request.app.state.settings
    async with aiosqlite.connect(str(settings.db_path)) as conn:
        await apply_pragmas(conn)
        yield conn


DbDep = Annotated[aiosqlite.Connection, Depends(get_db)]


def get_log_repo(db: DbDep) -> LogEventRepository:
    return LogEventRepository(db)


LogRepoDep = Annotated[LogEventRepository, Depends(get_log_repo)]


def get_ingestion_service(repo: LogRepoDep) -> IngestionService:
    return IngestionService(repo)


IngestionServiceDep = Annotated[IngestionService, Depends(get_ingestion_service)]
