"""Outbox dispatcher worker — at-least-once delivery via transactional outbox.

Single-worker by design
-----------------------
The competing-consumer pattern (multiple workers polling the same
queue concurrently) requires the DB to atomically claim rows with
`SELECT ... FOR UPDATE SKIP LOCKED`. **SQLite has no SKIP LOCKED.**
Running two workers against the same SQLite outbox would race on
the same row and either:
  * dual-deliver (one worker marks delivered while the other is
    mid-flight, then the second succeeds too), or
  * deadlock under load.

We commit to a single worker for the SQLite MVP. When ADR-001's
Postgres-migration trigger fires (sustained > 50 req/s), the worker
implementation grows to N replicas using
`SELECT ... FOR UPDATE SKIP LOCKED LIMIT 1` to claim a row
atomically. Until then, a single worker with a small poll interval
is correct and simple.

Retry policy
------------
Exponential backoff: 1s → 4s → 16s → 64s between attempts. After
5 total attempts (1 initial + 4 retries) the row is moved to
`dead_letter` and never re-dispatched. The schedule is configurable
to keep tests fast (tests pass `backoff_seconds=[0, 0, 0, 0]`).

Lifecycle
---------
`run()` is the long-running coroutine to schedule via
`asyncio.create_task` in FastAPI's lifespan. Cancelling the task
makes `run()` exit cleanly via the `asyncio.CancelledError` catch.
Tests drive the worker via `tick()` calls and skip the loop entirely.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import aiosqlite

from watchdog_core.persistence.migrations import apply_pragmas
from watchdog_core.persistence.repositories import AlertRepository, OutboxRepository

if TYPE_CHECKING:
    from watchdog_core.alerting.webhook_dispatcher import WebhookDispatcher

logger = logging.getLogger(__name__)

_DEFAULT_MAX_ATTEMPTS = 5
_DEFAULT_BACKOFF_SECONDS: tuple[float, ...] = (1.0, 4.0, 16.0, 64.0)
_DEFAULT_POLL_INTERVAL_S = 1.0
_DEFAULT_BATCH = 10


class OutboxWorker:
    """Single-worker outbox dispatcher loop."""

    def __init__(  # noqa: PLR0913 — orchestration seam needs all of these
        self,
        *,
        dispatcher: WebhookDispatcher,
        target_url: str,
        secret: str,
        db_path: str | None = None,
        conn: aiosqlite.Connection | None = None,
        max_attempts: int = _DEFAULT_MAX_ATTEMPTS,
        backoff_seconds: tuple[float, ...] = _DEFAULT_BACKOFF_SECONDS,
        poll_interval_seconds: float = _DEFAULT_POLL_INTERVAL_S,
        batch_size: int = _DEFAULT_BATCH,
    ) -> None:
        if conn is None and db_path is None:
            msg = "OutboxWorker requires either `conn` or `db_path`"
            raise ValueError(msg)
        self._dispatcher = dispatcher
        self._target_url = target_url
        self._secret = secret
        self._db_path = db_path
        self._conn = conn
        self._max_attempts = max_attempts
        self._backoff_seconds = backoff_seconds
        self._poll_interval = poll_interval_seconds
        self._batch_size = batch_size

    async def run(self) -> None:
        """Long-running loop. Cancellable via task.cancel()."""
        if self._conn is not None:
            await self._loop(self._conn)
            return

        if self._db_path is None:  # pragma: no cover  guarded in __init__
            msg = "OutboxWorker.run requires db_path when no conn is injected"
            raise RuntimeError(msg)

        async with aiosqlite.connect(self._db_path) as conn:
            await apply_pragmas(conn)
            await self._loop(conn)

    async def _loop(self, conn: aiosqlite.Connection) -> None:
        outbox_repo = OutboxRepository(conn)
        alert_repo = AlertRepository(conn)
        try:
            while True:
                try:
                    await self.tick(outbox_repo, alert_repo)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("outbox tick raised; continuing")
                await asyncio.sleep(self._poll_interval)
        except asyncio.CancelledError:
            logger.info("outbox worker cancelled; exiting cleanly")
            raise

    async def tick(
        self,
        outbox_repo: OutboxRepository,
        alert_repo: AlertRepository,
    ) -> int:
        """Process one batch. Returns the number of jobs handled.

        Public so tests can drive the worker deterministically.
        """
        jobs = await outbox_repo.claim_pending(limit=self._batch_size)
        if not jobs:
            return 0

        handled = 0
        for job_id, alert_id, _payload in jobs:
            attempts_before = await self._get_attempts(outbox_repo, job_id)
            attempt_number = attempts_before + 1
            alert = await alert_repo.get_by_id(alert_id)
            if alert is None:
                logger.warning(
                    "outbox row points at missing alert; dead-lettering",
                    extra={"job_id": job_id, "alert_id": str(alert_id)},
                )
                await outbox_repo.mark_dead_letter(job_id)
                handled += 1
                continue

            result = await self._dispatcher.dispatch(
                alert,
                self._target_url,
                self._secret,
                attempt=attempt_number,
            )

            if result.success:
                await outbox_repo.mark_delivered(job_id)
            elif attempt_number >= self._max_attempts:
                logger.warning(
                    "outbox job dead-lettered after max attempts",
                    extra={"job_id": job_id, "attempt": attempt_number, "error": result.error},
                )
                await outbox_repo.mark_dead_letter(job_id)
            else:
                # Schedule next attempt with backoff[attempts_before]
                # (first failure → backoff[0]).
                backoff = self._backoff_seconds[
                    min(attempts_before, len(self._backoff_seconds) - 1)
                ]
                next_at = datetime.now(UTC) + timedelta(seconds=backoff)
                await outbox_repo.mark_failed(job_id, next_attempt_at=next_at)
            handled += 1
        return handled

    @staticmethod
    async def _get_attempts(outbox_repo: OutboxRepository, job_id: int) -> int:
        cursor = await outbox_repo._conn.execute(  # noqa: SLF001 — internal coordination helper
            "SELECT attempts FROM webhook_outbox WHERE id = ?",
            (job_id,),
        )
        row = await cursor.fetchone()
        await cursor.close()
        return int(row[0]) if row is not None else 0
