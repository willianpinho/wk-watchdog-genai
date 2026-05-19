"""OutboxWorker dead-letter scenario — five 500s → `dead_letter`."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import aiosqlite
import pytest_asyncio
import respx
from httpx import Response

from watchdog_core.alerting.outbox_worker import OutboxWorker
from watchdog_core.alerting.webhook_dispatcher import WebhookDispatcher
from watchdog_core.domain.models import Alert, AnomalyWindow
from watchdog_core.persistence.migrations import apply_migrations
from watchdog_core.persistence.repositories import AlertRepository, OutboxRepository

_TARGET = "https://hooks.example.com/webhook"


@pytest_asyncio.fixture
async def conn(tmp_path: Path) -> AsyncIterator[aiosqlite.Connection]:
    db_path = tmp_path / "deadletter.sqlite"
    async with aiosqlite.connect(str(db_path)) as c:
        await apply_migrations(c)
        yield c


async def test_worker_five_500s_then_dead_letter(conn: aiosqlite.Connection) -> None:
    """After 5 failed attempts the job is moved to `dead_letter` and
    further ticks do NOT re-dispatch it."""
    alerts_repo = AlertRepository(conn)
    outbox_repo = OutboxRepository(conn)

    alert = Alert(
        id=uuid4(),
        anomaly=AnomalyWindow(
            service="api",
            level="CRITICAL",
            window_start=datetime(2026, 5, 19, 12, 0, 0, tzinfo=UTC),
            window_end=datetime(2026, 5, 19, 12, 1, 0, tzinfo=UTC),
            count=200,
            baseline_mean=5.0,
            baseline_stddev=2.0,
            z_score=97.0,
        ),
        severity="critical",
        reasoning="catastrophe",
        created_at=datetime.now(UTC),
    )
    await alerts_repo.insert(alert)
    job_id = await outbox_repo.enqueue(alert.id, {"alert_id": str(alert.id)})

    with respx.mock(assert_all_called=False) as router:
        route = router.post(_TARGET).mock(return_value=Response(500))

        dispatcher = WebhookDispatcher()
        try:
            worker = OutboxWorker(
                dispatcher=dispatcher,
                target_url=_TARGET,
                secret="shhh",  # noqa: S106 — test fixture, not a real credential
                conn=conn,
                backoff_seconds=(0.0, 0.0, 0.0, 0.0),
                max_attempts=5,
            )

            # 5 ticks → 5 attempts → dead_letter after the 5th.
            for _ in range(5):
                await worker.tick(outbox_repo, alerts_repo)

            assert route.call_count == 5

            # 6th tick — claim_pending should NOT return a dead-lettered
            # row, so the dispatcher MUST NOT be called again.
            await worker.tick(outbox_repo, alerts_repo)
            assert route.call_count == 5, "dead-lettered job was re-dispatched"
        finally:
            await dispatcher.aclose()

    cursor = await conn.execute(
        "SELECT status, attempts FROM webhook_outbox WHERE id = ?",
        (job_id,),
    )
    row = await cursor.fetchone()
    await cursor.close()
    assert row is not None
    status, attempts = row
    assert status == "dead_letter"
    # attempts increments only on mark_failed (which we call 4 times before
    # the 5th attempt that goes straight to mark_dead_letter without
    # incrementing).
    assert attempts == 4
