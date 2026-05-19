"""Dashboard routes — 200 status, sentinel strings, no 5xx on empty DB,
HTMX partials are HTML fragments (no `<html>` wrapper)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import aiosqlite
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from watchdog_api.config import Settings
from watchdog_api.main import create_app
from watchdog_core.domain.models import Alert, AnomalyWindow
from watchdog_core.persistence.migrations import apply_migrations
from watchdog_core.persistence.repositories import AlertRepository

_MAX_PAGE_WEIGHT_BYTES = 100_000


@pytest_asyncio.fixture
async def client(tmp_path: Path) -> AsyncIterator[tuple[AsyncClient, str]]:
    db_path = tmp_path / "dashboard.sqlite"
    settings = Settings(db_url=f"sqlite+aiosqlite:///{db_path}", otel_enabled=False)
    app = create_app(settings)
    async with (
        app.router.lifespan_context(app),
        AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as c,
    ):
        yield c, str(db_path)


async def test_dashboard_root_returns_200_on_empty_db(client) -> None:  # type: ignore[no-untyped-def]
    c, _ = client
    r = await c.get("/")
    assert r.status_code == 200
    body = r.text
    assert "wk-watchdog" in body
    assert "Overview" in body
    assert "Event rate" in body
    assert "Recent alerts" in body


async def test_dashboard_root_renders_top_services_after_ingest(client) -> None:  # type: ignore[no-untyped-def]
    c, _ = client
    payload = {
        "events": [
            {
                "ts": "2026-05-19T12:00:00+00:00",
                "service": "noisy-service",
                "level": "ERROR",
                "message": f"msg-{i}",
                "attributes": {},
            }
            for i in range(3)
        ],
    }
    ingest = await c.post("/v1/events", json=payload)
    assert ingest.status_code == 202, ingest.text
    # The first event is accepted; the rest dedupe within the 5 s window
    # since ts is identical. We only need one event to make the service
    # show up in the "top services" panel.

    r = await c.get("/")
    assert r.status_code == 200
    assert "noisy-service" in r.text


async def test_partial_event_rate_returns_uplot_columnar_json(client) -> None:  # type: ignore[no-untyped-def]
    c, _ = client
    r = await c.get("/partials/event-rate")
    assert r.status_code == 200
    body = r.json()
    assert set(body.keys()) == {"ts", "counts"}
    # 60-minute window with one entry per minute.
    assert len(body["ts"]) == 60
    assert len(body["counts"]) == 60
    assert all(isinstance(x, int) for x in body["ts"])
    assert all(isinstance(x, int) for x in body["counts"])


async def test_partial_alerts_returns_html_fragment_not_full_document(client) -> None:  # type: ignore[no-untyped-def]
    c, _ = client
    r = await c.get("/partials/alerts")
    assert r.status_code == 200
    body = r.text
    lowered = body.lower()
    # HTMX partials MUST NOT carry the full document chrome — they're
    # swapped into an existing target.
    assert "<!doctype" not in lowered
    assert "<html" not in lowered
    assert "<body" not in lowered


async def test_alert_detail_renders_and_handles_404(client) -> None:  # type: ignore[no-untyped-def]
    c, db_path = client

    # Insert one alert directly via the repository.
    async with aiosqlite.connect(db_path) as conn:
        await apply_migrations(conn)
        repo = AlertRepository(conn)
        alert = Alert(
            id=uuid4(),
            anomaly=AnomalyWindow(
                service="api",
                level="ERROR",
                window_start=datetime(2026, 5, 19, 12, 0, 0, tzinfo=UTC),
                window_end=datetime(2026, 5, 19, 12, 1, 0, tzinfo=UTC),
                count=80,
                baseline_mean=4.0,
                baseline_stddev=2.0,
                z_score=38.0,
            ),
            severity="high",
            reasoning="spike-reason",
            created_at=datetime.now(UTC),
        )
        await repo.insert(alert)

    r = await c.get(f"/alerts/{alert.id}")
    assert r.status_code == 200
    assert "spike-reason" in r.text
    assert "Anomaly window" in r.text

    # Unknown id → 404
    bogus = await c.get(f"/alerts/{uuid4()}")
    assert bogus.status_code == 404


async def test_page_weight_under_100kb(client) -> None:  # type: ignore[no-untyped-def]
    """HTML + vendored uPlot CSS/JS combined stays under 100 KB."""
    c, _ = client
    html = (await c.get("/")).content
    css = (await c.get("/static/uPlot.min.css")).content
    js = (await c.get("/static/uPlot.iife.min.js")).content
    total = len(html) + len(css) + len(js)
    assert total < _MAX_PAGE_WEIGHT_BYTES, (
        f"page weight {total} exceeds {_MAX_PAGE_WEIGHT_BYTES} bytes "
        f"(html={len(html)}, css={len(css)}, js={len(js)})"
    )
