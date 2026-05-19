"""Ingestion service: business rules for accepting log events.

This module MUST NOT import any persistence backend directly (no
`aiosqlite`, no `sqlalchemy`). It depends only on the
`LogEventRepositoryProtocol`, which is satisfied by the concrete
`LogEventRepository` from `watchdog_core.persistence.repositories`
AND by in-memory fakes in tests.

Business invariants enforced
----------------------------
1. Reject events whose `ts` is older than `MAX_EVENT_AGE` (24h).
2. Reject events with a naive (tz-unaware) `ts`.
3. Reject empty service names; normalize `service` to a trimmed
   lowercase string.
4. Dedupe by `(service, ts, message_hash)`: if the repo reports a
   matching event whose `ts` is within `DEDUPE_WINDOW` (5s) of the
   incoming one, return `None` instead of inserting.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import uuid4

from watchdog_core.domain.hashing import compute_message_hash
from watchdog_core.domain.models import LogEvent, LogLevel

MAX_EVENT_AGE: timedelta = timedelta(hours=24)
DEDUPE_WINDOW: timedelta = timedelta(seconds=5)


class EventRejectedError(ValueError):
    """Raised when an ingested event violates a business invariant."""


class LogEventRepositoryProtocol(Protocol):
    """Persistence surface required by the ingestion service.

    Tests supply in-memory fakes implementing this Protocol.
    Production uses the aiosqlite-backed `LogEventRepository`.
    """

    async def insert(self, event: LogEvent) -> None: ...

    async def find_duplicate(
        self,
        *,
        service: str,
        ts: datetime,
        message_hash: str,
        window: timedelta,
    ) -> LogEvent | None: ...


def _utcnow() -> datetime:
    return datetime.now(UTC)


class IngestionService:
    """Accept-or-reject ingestion with dedupe.

    Returns the persisted `LogEvent` on success, `None` when deduped,
    and raises `EventRejectedError` on invariant violations.
    """

    def __init__(
        self,
        repo: LogEventRepositoryProtocol,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._repo = repo
        self._now: Callable[[], datetime] = now or _utcnow

    async def ingest(
        self,
        *,
        service: str,
        level: LogLevel,
        message: str,
        ts: datetime,
        attributes: dict[str, str] | None = None,
    ) -> LogEvent | None:
        normalized = service.strip().lower()
        if not normalized:
            msg = "service must be non-empty"
            raise EventRejectedError(msg)

        if ts.tzinfo is None:
            msg = "ts must be timezone-aware (UTC)"
            raise EventRejectedError(msg)
        ts_utc = ts.astimezone(UTC)

        age = self._now() - ts_utc
        if age > MAX_EVENT_AGE:
            msg = f"event ts is {age} old; max age is {MAX_EVENT_AGE}"
            raise EventRejectedError(msg)

        msg_hash = compute_message_hash(message)
        existing = await self._repo.find_duplicate(
            service=normalized,
            ts=ts_utc,
            message_hash=msg_hash,
            window=DEDUPE_WINDOW,
        )
        if existing is not None:
            return None

        event = LogEvent(
            id=uuid4(),
            ts=ts_utc,
            service=normalized,
            level=level,
            message=message,
            attributes=attributes or {},
        )
        await self._repo.insert(event)
        return event
