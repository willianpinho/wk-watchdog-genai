"""OTel smoke test — assert the span hierarchy emitted by POST /v1/events.

We install a `SimpleSpanProcessor` backed by `InMemorySpanExporter`
BEFORE `create_app` runs (via the `span_processor` kwarg), so every
span the app emits lands in the exporter's buffer. The test then
asserts the hierarchy required by the brief:

    POST /v1/events
      └─ IngestionService.ingest_batch
          └─ LogEventRepository.insert_many
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import opentelemetry.trace as _otel_trace
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.util._once import Once

from watchdog_api.config import Settings
from watchdog_api.main import create_app


@pytest_asyncio.fixture
async def otel_client(tmp_path: Path) -> AsyncIterator[tuple[AsyncClient, InMemorySpanExporter]]:
    # OTel's `set_tracer_provider` is "set once" — re-runs warn + keep
    # the first provider. We reset both the `_TRACER_PROVIDER` slot AND
    # the Once guard so our InMemorySpanExporter wins regardless of
    # which test ran before this one.
    _otel_trace._TRACER_PROVIDER = None  # noqa: SLF001 — only stable reset path
    _otel_trace._TRACER_PROVIDER_SET_ONCE = Once()  # noqa: SLF001

    db_path = tmp_path / "otel.sqlite"
    settings = Settings(db_url=f"sqlite+aiosqlite:///{db_path}")
    exporter = InMemorySpanExporter()
    app = create_app(settings, span_processor=SimpleSpanProcessor(exporter))
    async with (
        app.router.lifespan_context(app),
        AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as c,
    ):
        yield c, exporter


async def test_span_hierarchy_post_ingest_batch_insert_many(otel_client) -> None:  # type: ignore[no-untyped-def]
    client, exporter = otel_client
    payload = {
        "events": [
            {
                "ts": "2026-05-19T12:00:00+00:00",
                "service": "trace-test",
                "level": "INFO",
                "message": "hello-otel",
                "attributes": {},
            },
        ],
    }
    r = await client.post("/v1/events", json=payload)
    assert r.status_code == 202, r.text

    spans = exporter.get_finished_spans()
    span_names = {s.name for s in spans}

    # The brief's required hierarchy:
    assert (
        "IngestionService.ingest_batch" in span_names
    ), f"missing ingest_batch span; saw {span_names}"
    assert (
        "LogEventRepository.insert_many" in span_names
    ), f"missing insert_many span; saw {span_names}"

    # FastAPIInstrumentor emits a server span for the route — the exact
    # name varies across versions, so we assert the *presence* of any
    # span that mentions /v1/events.
    assert any(
        "/v1/events" in s.name or "v1/events" in s.name or s.name == "POST" for s in spans
    ), f"missing FastAPI server span for /v1/events; saw {span_names}"

    # Parent-child relationship: insert_many is a child of ingest_batch.
    by_id = {s.context.span_id: s for s in spans}
    insert_many = next(s for s in spans if s.name == "LogEventRepository.insert_many")
    parent = by_id.get(insert_many.parent.span_id) if insert_many.parent else None
    assert parent is not None
    assert parent.name == "IngestionService.ingest_batch"
