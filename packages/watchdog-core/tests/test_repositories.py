"""Repository tests — real aiosqlite, file-backed for WAL verification."""

from __future__ import annotations

import inspect
import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import aiosqlite
import pytest_asyncio

from watchdog_core.domain.hashing import compute_message_hash
from watchdog_core.domain.models import Alert, AnomalyWindow, LogEvent
from watchdog_core.persistence import repositories as repo_mod
from watchdog_core.persistence.migrations import MIGRATIONS, apply_migrations
from watchdog_core.persistence.repositories import (
    AlertRepository,
    LogEventRepository,
    OutboxRepository,
)


@pytest_asyncio.fixture
async def conn(tmp_path: Path) -> AsyncIterator[aiosqlite.Connection]:
    db_path = tmp_path / "test.sqlite"
    async with aiosqlite.connect(str(db_path)) as c:
        await apply_migrations(c)
        yield c


# ---------------------------------------------------------------------------
# Migration mechanics
# ---------------------------------------------------------------------------


async def test_migrations_recorded(conn: aiosqlite.Connection) -> None:
    cursor = await conn.execute("SELECT version FROM schema_migrations ORDER BY version")
    rows = await cursor.fetchall()
    assert [int(r[0]) for r in rows] == [v for v, _ in MIGRATIONS]


async def test_wal_mode_active(conn: aiosqlite.Connection) -> None:
    cursor = await conn.execute("PRAGMA journal_mode")
    row = await cursor.fetchone()
    assert row is not None
    assert str(row[0]).lower() == "wal"


async def test_foreign_keys_enabled(conn: aiosqlite.Connection) -> None:
    cursor = await conn.execute("PRAGMA foreign_keys")
    row = await cursor.fetchone()
    assert row is not None
    assert int(row[0]) == 1


async def test_indices_exist(conn: aiosqlite.Connection) -> None:
    cursor = await conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'index' AND name LIKE 'idx_%'",
    )
    names = {row[0] for row in await cursor.fetchall()}
    required = {
        "idx_log_events_service_ts",
        "idx_log_events_service_level_ts",
        "idx_log_events_dedupe",
        "idx_alerts_service_ts",
        "idx_alerts_webhook_status",
        "idx_webhook_outbox_status_next",
    }
    missing = required - names
    assert not missing, f"missing indices: {missing}"


async def test_migration_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "idempotent.sqlite"
    async with aiosqlite.connect(str(db_path)) as first:
        n1 = await apply_migrations(first)
    async with aiosqlite.connect(str(db_path)) as second:
        n2 = await apply_migrations(second)
    assert n1 == len(MIGRATIONS)
    assert n2 == 0


# ---------------------------------------------------------------------------
# LogEventRepository
# ---------------------------------------------------------------------------


async def test_log_event_insert_then_list_recent(conn: aiosqlite.Connection) -> None:
    repo = LogEventRepository(conn)
    event = LogEvent(
        id=uuid4(),
        ts=datetime(2026, 5, 19, 12, 0, 0, tzinfo=UTC),
        service="auth-api",
        level="ERROR",
        message="db timeout",
        attributes={"trace_id": "abc"},
    )
    await repo.insert(event)
    recent = await repo.list_recent("auth-api")
    assert len(recent) == 1
    got = recent[0]
    assert got.id == event.id
    assert got.message == event.message
    assert got.attributes == {"trace_id": "abc"}


async def test_log_event_count_in_window(conn: aiosqlite.Connection) -> None:
    repo = LogEventRepository(conn)
    base = datetime(2026, 5, 19, 12, 0, 0, tzinfo=UTC)
    for i in range(5):
        await repo.insert(
            LogEvent(
                id=uuid4(),
                ts=base + timedelta(minutes=i),
                service="api",
                level="ERROR",
                message=f"err-{i}",
            ),
        )
    count = await repo.count_in_window(
        "api",
        "ERROR",
        window_start=base,
        window_end=base + timedelta(minutes=5),
    )
    assert count == 5


async def test_log_event_find_duplicate_within_window(conn: aiosqlite.Connection) -> None:
    repo = LogEventRepository(conn)
    base = datetime(2026, 5, 19, 12, 0, 0, tzinfo=UTC)
    msg = "hello"
    await repo.insert(
        LogEvent(
            id=uuid4(),
            ts=base,
            service="api",
            level="WARN",
            message=msg,
        ),
    )
    dup = await repo.find_duplicate(
        service="api",
        ts=base + timedelta(seconds=3),
        message_hash=compute_message_hash(msg),
        window=timedelta(seconds=5),
    )
    assert dup is not None
    assert dup.message == msg


async def test_log_event_no_duplicate_outside_window(conn: aiosqlite.Connection) -> None:
    repo = LogEventRepository(conn)
    base = datetime(2026, 5, 19, 12, 0, 0, tzinfo=UTC)
    msg = "hello"
    await repo.insert(
        LogEvent(
            id=uuid4(),
            ts=base,
            service="api",
            level="WARN",
            message=msg,
        ),
    )
    dup = await repo.find_duplicate(
        service="api",
        ts=base + timedelta(seconds=60),
        message_hash=compute_message_hash(msg),
        window=timedelta(seconds=5),
    )
    assert dup is None


# ---------------------------------------------------------------------------
# AlertRepository + OutboxRepository
# ---------------------------------------------------------------------------


def _sample_anomaly() -> AnomalyWindow:
    return AnomalyWindow(
        service="api",
        level="ERROR",
        window_start=datetime(2026, 5, 19, 11, 55, 0, tzinfo=UTC),
        window_end=datetime(2026, 5, 19, 12, 0, 0, tzinfo=UTC),
        count=10,
        baseline_mean=2.0,
        baseline_stddev=1.0,
        z_score=8.0,
    )


def _sample_alert() -> Alert:
    return Alert(
        id=uuid4(),
        anomaly=_sample_anomaly(),
        severity="critical",
        reasoning="Spike of ERROR events vs 7-day baseline.",
        created_at=datetime(2026, 5, 19, 12, 0, 0, tzinfo=UTC),
    )


async def test_alert_insert_and_list_pending(conn: aiosqlite.Connection) -> None:
    repo = AlertRepository(conn)
    alert = _sample_alert()
    await repo.insert(alert)
    pending = await repo.list_pending()
    assert len(pending) == 1
    assert pending[0].id == alert.id
    assert pending[0].anomaly.count == 10


async def test_outbox_lifecycle(conn: aiosqlite.Connection) -> None:
    alert_repo = AlertRepository(conn)
    alert = _sample_alert()
    await alert_repo.insert(alert)

    outbox = OutboxRepository(conn)
    job_id = await outbox.enqueue(alert.id, {"alert_id": str(alert.id), "x": 1})
    assert job_id > 0

    pending = await outbox.claim_pending()
    assert len(pending) == 1
    job_id_got, alert_id_got, payload = pending[0]
    assert job_id_got == job_id
    assert alert_id_got == alert.id
    assert payload["x"] == 1

    await outbox.mark_delivered(job_id)
    after = await outbox.claim_pending()
    assert after == []


async def test_outbox_failure_then_dead_letter(conn: aiosqlite.Connection) -> None:
    alert_repo = AlertRepository(conn)
    alert = _sample_alert()
    await alert_repo.insert(alert)

    outbox = OutboxRepository(conn)
    job_id = await outbox.enqueue(alert.id, {"payload": "x"})

    next_at = datetime.now(UTC) + timedelta(minutes=5)
    await outbox.mark_failed(job_id, next_attempt_at=next_at)

    # Still 'failed', but next_attempt_at is in the future — should NOT be
    # claimable yet.
    later = await outbox.claim_pending()
    assert later == []

    await outbox.mark_dead_letter(job_id)
    cursor = await conn.execute(
        "SELECT status FROM webhook_outbox WHERE id = ?",
        (job_id,),
    )
    row = await cursor.fetchone()
    assert row is not None
    assert row[0] == "dead_letter"


# ---------------------------------------------------------------------------
# Defense-in-depth: source-grep that repositories use ? placeholders only.
# ---------------------------------------------------------------------------


def test_repositories_use_parameterized_queries() -> None:
    src = inspect.getsource(repo_mod)
    pattern = re.compile(r"\.execute\(\s*f['\"]", re.DOTALL)
    assert not pattern.search(src), (
        "Found `.execute(f'...` pattern in repositories.py. Repositories "
        "must use ? placeholders, not f-strings, to prevent SQL injection."
    )
