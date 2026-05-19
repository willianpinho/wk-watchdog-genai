"""OutboxWorker retry-success scenario — 500 → 500 → 200 → delivered."""

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
    db_path = tmp_path / "worker.sqlite"
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
            count=80,
            baseline_mean=4.0,
            baseline_stddev=2.0,
            z_score=38.0,
        ),
        severity="high",
        reasoning="spike",
        created_at=datetime.now(UTC),
    )


async def test_worker_500_500_200_marks_delivered(conn: aiosqlite.Connection) -> None:
    alerts_repo = AlertRepository(conn)
    outbox_repo = OutboxRepository(conn)
    alert = _sample_alert()
    await alerts_repo.insert(alert)
    job_id = await outbox_repo.enqueue(alert.id, {"alert_id": str(alert.id)})

    with respx.mock(assert_all_called=False) as router:
        route = router.post(_TARGET).mock(
            side_effect=[
                Response(500),
                Response(500),
                Response(200, json={"received": True}),
            ],
        )

        dispatcher = WebhookDispatcher()
        try:
            worker = OutboxWorker(
                dispatcher=dispatcher,
                target_url=_TARGET,
                secret="shhh",  # noqa: S106 — test fixture, not a real credential
                conn=conn,
                backoff_seconds=(0.0, 0.0, 0.0, 0.0),  # no waiting in tests
            )

            # First tick → attempt 1 (500) → mark_failed, backoff scheduled at "now".
            handled1 = await worker.tick(outbox_repo, alerts_repo)
            assert handled1 == 1

            # Second tick → attempt 2 (500) → mark_failed again.
            handled2 = await worker.tick(outbox_repo, alerts_repo)
            assert handled2 == 1

            # Third tick → attempt 3 (200) → mark_delivered.
            handled3 = await worker.tick(outbox_repo, alerts_repo)
            assert handled3 == 1

            assert route.call_count == 3
        finally:
            await dispatcher.aclose()

    # Verify final state.
    cursor = await conn.execute(
        "SELECT status, attempts FROM webhook_outbox WHERE id = ?",
        (job_id,),
    )
    row = await cursor.fetchone()
    await cursor.close()
    assert row is not None
    status, attempts = row
    assert status == "delivered"
    # attempts column was incremented twice (after the two 500s); the
    # final success does NOT increment it (we use mark_delivered).
    assert attempts == 2


async def test_worker_ignores_outbox_pointing_at_missing_alert(
    conn: aiosqlite.Connection,
) -> None:
    """If an outbox row references an alert that no longer exists,
    dead-letter the job rather than dispatch garbage."""
    outbox_repo = OutboxRepository(conn)
    alerts_repo = AlertRepository(conn)

    # Insert a real alert so the FK lets us enqueue, then DELETE the alert
    # via a raw query (bypassing FK enforcement won't work; instead we
    # disable foreign_keys temporarily for this surgical-test setup).
    alert = _sample_alert()
    await alerts_repo.insert(alert)
    job_id = await outbox_repo.enqueue(alert.id, {"alert_id": str(alert.id)})
    await conn.execute("PRAGMA foreign_keys = OFF")
    await conn.execute("DELETE FROM alerts WHERE id = ?", (str(alert.id),))
    await conn.commit()
    await conn.execute("PRAGMA foreign_keys = ON")

    dispatcher = WebhookDispatcher()
    try:
        worker = OutboxWorker(
            dispatcher=dispatcher,
            target_url=_TARGET,
            secret="shhh",  # noqa: S106 — test fixture
            conn=conn,
            backoff_seconds=(0.0, 0.0, 0.0, 0.0),
        )
        await worker.tick(outbox_repo, alerts_repo)
    finally:
        await dispatcher.aclose()

    cursor = await conn.execute(
        "SELECT status FROM webhook_outbox WHERE id = ?",
        (job_id,),
    )
    row = await cursor.fetchone()
    await cursor.close()
    assert row is not None
    assert row[0] == "dead_letter"
