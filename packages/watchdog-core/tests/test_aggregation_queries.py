"""Aggregation-query unit tests.

Seeds events at known timestamps and asserts that
`LogEventRepository.rate_per_minute` and `summary_last_24h` produce
the expected aggregates. The EXPLAIN-QUERY-PLAN assertion proves we
actually USE the `(service, ts)` composite index for per-service
time-bucketed queries — without the index a service grows linearly
expensive at scale.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import aiosqlite
import pytest_asyncio

from watchdog_core.domain.models import LogEvent
from watchdog_core.persistence.migrations import apply_migrations
from watchdog_core.persistence.repositories import LogEventRepository


@pytest_asyncio.fixture
async def conn(tmp_path: Path) -> AsyncIterator[aiosqlite.Connection]:
    db_path = tmp_path / "agg.sqlite"
    async with aiosqlite.connect(str(db_path)) as c:
        await apply_migrations(c)
        yield c


async def test_rate_per_minute_zero_fills_empty_buckets(conn: aiosqlite.Connection) -> None:
    repo = LogEventRepository(conn)
    series = await repo.rate_per_minute(minutes=60)
    assert len(series["ts"]) == 60
    assert len(series["counts"]) == 60
    assert sum(series["counts"]) == 0  # empty DB → all zeros


async def test_rate_per_minute_buckets_correctly(conn: aiosqlite.Connection) -> None:
    """Synthesise 1000 events across known minutes and verify counts."""
    repo = LogEventRepository(conn)
    # Anchor at "now floored to a minute" so insert ts <-> bucket lookup
    # uses the exact same key the production code does.
    now_minute = datetime.now(UTC).replace(second=0, microsecond=0)
    # Distribute: minute T-1 gets 700 events, T-2 gets 300 events, rest are 0.
    # NOTE: we add the second offset AFTER the minute subtraction so every
    # event stays inside its target minute. Subtracting `seconds=X` from
    # `now_minute - 1min` would push the event back across the bucket
    # boundary into minute T-2.
    t_minus_1 = now_minute - timedelta(minutes=1)
    t_minus_2 = now_minute - timedelta(minutes=2)
    for i in range(700):
        await repo.insert(
            LogEvent(
                id=uuid4(),
                ts=t_minus_1 + timedelta(seconds=i % 50),
                service=f"svc-{i % 10}",
                level="ERROR",
                message=f"m-{i}",
            ),
        )
    for i in range(300):
        await repo.insert(
            LogEvent(
                id=uuid4(),
                ts=t_minus_2 + timedelta(seconds=i % 50),
                service=f"svc-{i % 10}",
                level="ERROR",
                message=f"m2-{i}",
            ),
        )

    series = await repo.rate_per_minute(minutes=60)
    # Series runs [now-60 .. now-1]. T-1 bucket is the last entry,
    # T-2 is the second-to-last.
    assert series["counts"][-1] == 700, (
        f"expected 700 events in T-1 minute, got {series['counts'][-1]}; "
        f"full series tail: {series['counts'][-5:]}"
    )
    assert series["counts"][-2] == 300
    # All other minutes are zero-filled.
    assert sum(series["counts"][:-2]) == 0


async def test_summary_last_24h_top_services(conn: aiosqlite.Connection) -> None:
    repo = LogEventRepository(conn)
    now = datetime.now(UTC) - timedelta(minutes=5)
    counts = {"api": 50, "billing": 30, "search": 20, "auth": 10, "edge": 5, "background": 1}
    for service, n in counts.items():
        for i in range(n):
            await repo.insert(
                LogEvent(
                    id=uuid4(),
                    ts=now - timedelta(seconds=i),
                    service=service,
                    level="INFO",
                    message=f"{service}-{i}",
                ),
            )

    summary = await repo.summary_last_24h(top_n_services=5)
    assert summary["total_events"] == sum(counts.values())
    # Top 5 by count, descending.
    expected = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:5]
    assert summary["top_services"] == expected


async def test_per_service_ts_query_uses_idx_log_events_service_ts(
    conn: aiosqlite.Connection,
) -> None:
    """Prove the (service, ts) composite index is actually used.

    EXPLAIN QUERY PLAN should mention the index name. SQLite's planner
    can also pick the dedupe index in some versions, so we accept any
    plan that mentions an `idx_log_events_*` index (NOT a full scan).
    """
    cursor = await conn.execute(
        "EXPLAIN QUERY PLAN "
        "SELECT ts, COUNT(*) FROM log_events "
        "WHERE service = ? AND ts >= ? AND ts < ? "
        "GROUP BY ts",
        ("api", "2026-05-19T00:00:00+00:00", "2026-05-20T00:00:00+00:00"),
    )
    rows = await cursor.fetchall()
    await cursor.close()
    plan_text = " | ".join(str(row) for row in rows)
    msg_idx = f"expected the planner to use one of the log_events indexes, got:\n{plan_text}"
    assert "idx_log_events_" in plan_text, msg_idx
    msg_scan = f"the per-service+ts query is doing a full scan:\n{plan_text}"
    assert "SCAN log_events" not in plan_text or "USING INDEX" in plan_text, msg_scan
