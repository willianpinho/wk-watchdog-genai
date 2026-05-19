"""Shared fixtures for apps/api tests."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from watchdog_api.config import Settings
from watchdog_api.main import create_app


@pytest_asyncio.fixture
async def client(tmp_path: Path) -> AsyncIterator[AsyncClient]:
    """An httpx AsyncClient with a fresh app + temp SQLite, lifespan run."""
    db_path = tmp_path / "test.sqlite"
    # otel_enabled=False keeps these tests offline (no OTLP exporter
    # poking localhost:4318) AND avoids stealing OTel's "set tracer
    # provider once" slot from test_otel_smoke, which depends on
    # being the first to install a SimpleSpanProcessor.
    settings = Settings(db_url=f"sqlite+aiosqlite:///{db_path}", otel_enabled=False)
    app = create_app(settings)
    async with (
        app.router.lifespan_context(app),
        AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as c,
    ):
        yield c
