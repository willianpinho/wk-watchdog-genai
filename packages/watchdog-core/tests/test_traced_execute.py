"""Cover the `traced_execute` helper.

`watchdog_core.observability.tracing.traced_execute` wraps an aiosqlite
`Connection.execute()` in an OTel span carrying `db.system=sqlite` and
`db.statement=<sql>` attributes. The helper is intentionally tiny;
this test simply verifies it returns a usable cursor and emits a span.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import aiosqlite
import pytest_asyncio
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.util._once import Once

from watchdog_core.observability.tracing import traced_execute


@pytest_asyncio.fixture
async def conn(tmp_path: Path) -> AsyncIterator[aiosqlite.Connection]:
    # Reset OTel global slot so our InMemorySpanExporter wins regardless
    # of test ordering (same trick as test_otel_smoke.py).
    trace._TRACER_PROVIDER = None  # noqa: SLF001
    trace._TRACER_PROVIDER_SET_ONCE = Once()  # noqa: SLF001

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

    db_path = tmp_path / "tracing.sqlite"
    async with aiosqlite.connect(str(db_path)) as c:
        await c.execute(
            "CREATE TABLE IF NOT EXISTS test_traced(id INTEGER PRIMARY KEY, value TEXT)",
        )
        await c.commit()
        c.__test_exporter__ = exporter  # type: ignore[attr-defined]
        yield c


async def test_traced_execute_returns_cursor_and_emits_span(conn: aiosqlite.Connection) -> None:
    exporter: InMemorySpanExporter = conn.__test_exporter__  # type: ignore[attr-defined]
    exporter.clear()

    cursor = await traced_execute(
        conn,
        "INSERT INTO test_traced(value) VALUES (?)",
        ("hello",),
    )
    await cursor.close()
    await conn.commit()

    select_cursor = await traced_execute(conn, "SELECT COUNT(*) FROM test_traced")
    row = await select_cursor.fetchone()
    await select_cursor.close()
    assert row is not None
    assert int(row[0]) == 1

    spans = exporter.get_finished_spans()
    sqlite_spans = [s for s in spans if s.name == "sqlite.execute"]
    assert (
        len(sqlite_spans) >= 2
    ), f"expected ≥2 sqlite.execute spans, saw {[s.name for s in spans]}"
    for s in sqlite_spans:
        assert s.attributes is not None
        assert s.attributes.get("db.system") == "sqlite"
        stmt = s.attributes.get("db.statement")
        assert isinstance(stmt, str) and stmt
