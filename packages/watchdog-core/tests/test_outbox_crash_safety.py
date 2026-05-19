"""Crash-safety: `AlertService.create_and_enqueue` is atomic.

The whole point of the transactional outbox pattern is that a failure
between the alert-insert and the outbox-insert cannot leave the DB in
a half-written state. We simulate that crash by patching the outbox
repository to raise mid-transaction, and assert that NEITHER row
survived.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import aiosqlite
import pytest
import pytest_asyncio

from watchdog_core.domain.models import Alert, AnomalyWindow
from watchdog_core.genai.severity_classifier import SeverityDecision
from watchdog_core.persistence.migrations import apply_migrations
from watchdog_core.persistence.repositories import AlertRepository, OutboxRepository
from watchdog_core.services.alert_service import AlertService


class _StubClassifier:
    async def classify(
        self,
        anomaly: AnomalyWindow,
        recent_messages: list[str],
    ) -> SeverityDecision:
        _ = (anomaly, recent_messages)
        return SeverityDecision(
            severity="high",
            reasoning="stub",
            confidence=0.9,
            suggested_action="x",
            model="stub",
            latency_ms=1,
        )


@pytest_asyncio.fixture
async def conn(tmp_path: Path) -> AsyncIterator[aiosqlite.Connection]:
    db_path = tmp_path / "crash.sqlite"
    async with aiosqlite.connect(str(db_path)) as c:
        await apply_migrations(c)
        yield c


def _sample_alert() -> Alert:
    return Alert(
        id=uuid4(),
        anomaly=AnomalyWindow(
            service="api",
            level="ERROR",
            window_start=datetime(2026, 5, 19, 12, 0, 0, tzinfo=UTC),
            window_end=datetime(2026, 5, 19, 12, 1, 0, tzinfo=UTC),
            count=50,
            baseline_mean=4.0,
            baseline_stddev=2.0,
            z_score=23.0,
        ),
        severity="high",
        reasoning="spike",
        created_at=datetime.now(UTC),
    )


async def test_create_and_enqueue_is_atomic_when_outbox_fails(
    conn: aiosqlite.Connection,
) -> None:
    """If the outbox-insert raises mid-transaction, the alert-insert
    must be rolled back — DB returns to an empty state."""
    alert_repo = AlertRepository(conn)
    outbox_repo = OutboxRepository(conn)

    # Patch the outbox repo to raise on enqueue (simulates the crash).
    async def failing_enqueue(
        alert_id: UUID,
        payload: dict[str, Any],
        *,
        commit: bool = True,
    ) -> int:
        _ = (alert_id, payload, commit)
        msg = "simulated crash mid-transaction"
        raise RuntimeError(msg)

    outbox_repo.enqueue = failing_enqueue  # type: ignore[method-assign]

    service = AlertService(
        uow=conn,  # aiosqlite.Connection satisfies UnitOfWorkProtocol
        alert_repo=alert_repo,
        outbox_repo=outbox_repo,
        classifier=_StubClassifier(),
    )

    alert = _sample_alert()
    with pytest.raises(RuntimeError, match="simulated crash"):
        await service.create_and_enqueue(alert, {"alert_id": str(alert.id)})

    # Neither row should be present.
    cursor = await conn.execute("SELECT COUNT(*) FROM alerts")
    row = await cursor.fetchone()
    await cursor.close()
    assert row is not None
    assert row[0] == 0, "alert row leaked despite outbox failure (not atomic)"

    cursor = await conn.execute("SELECT COUNT(*) FROM webhook_outbox")
    row = await cursor.fetchone()
    await cursor.close()
    assert row is not None
    assert row[0] == 0


async def test_create_and_enqueue_happy_path_writes_both_rows(
    conn: aiosqlite.Connection,
) -> None:
    alert_repo = AlertRepository(conn)
    outbox_repo = OutboxRepository(conn)
    service = AlertService(
        uow=conn,
        alert_repo=alert_repo,
        outbox_repo=outbox_repo,
        classifier=_StubClassifier(),
    )
    alert = _sample_alert()
    await service.create_and_enqueue(alert, {"alert_id": str(alert.id)})

    cursor = await conn.execute("SELECT COUNT(*) FROM alerts")
    row = await cursor.fetchone()
    await cursor.close()
    assert row is not None
    assert row[0] == 1

    cursor = await conn.execute("SELECT COUNT(*) FROM webhook_outbox")
    row = await cursor.fetchone()
    await cursor.close()
    assert row is not None
    assert row[0] == 1
