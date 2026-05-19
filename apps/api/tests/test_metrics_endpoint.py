"""`GET /metrics` returns Prometheus exposition format with our custom series."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from watchdog_api.config import Settings
from watchdog_api.main import create_app


@pytest_asyncio.fixture
async def client(tmp_path: Path) -> AsyncIterator[AsyncClient]:
    db_path = tmp_path / "metrics.sqlite"
    # otel_enabled=False keeps the test fully offline (no OTLP exporter
    # trying to connect to localhost:4318). /metrics still works because
    # the prometheus_client registry is independent of the OTel
    # MeterProvider in this codebase.
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


async def test_metrics_endpoint_returns_prometheus_format(client: AsyncClient) -> None:
    r = await client.get("/metrics")
    assert r.status_code == 200
    content_type = r.headers.get("content-type", "")
    # Prometheus exposition is `text/plain; version=0.0.4; charset=utf-8`.
    assert content_type.startswith("text/plain"), content_type

    body = r.text
    # Our custom series should be registered even before any traffic hits
    # them (the modules import-register on first import).
    for metric_name in (
        "events_ingested_total",
        "anomalies_detected_total",
        "webhook_delivery_latency_seconds",
        "genai_tokens_total",
        "outbox_queue_depth",
    ):
        assert metric_name in body, f"missing series '{metric_name}' in /metrics:\n{body[:500]}"


async def test_metrics_increment_after_ingest(client: AsyncClient) -> None:
    """After a successful POST /v1/events, events_ingested_total{...} > 0."""
    payload = {
        "events": [
            {
                "ts": "2026-05-19T12:00:00+00:00",
                "service": "metrics-test",
                "level": "ERROR",
                "message": "boom",
                "attributes": {},
            },
        ],
    }
    r = await client.post("/v1/events", json=payload)
    assert r.status_code == 202, r.text

    m = await client.get("/metrics")
    body = m.text
    assert 'events_ingested_total{level="ERROR",service="metrics-test"}' in body, body[:1000]
