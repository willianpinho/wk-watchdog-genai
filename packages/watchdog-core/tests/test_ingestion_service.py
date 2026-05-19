"""IngestionService unit tests using an in-memory fake Protocol impl.

The fake repo is the canonical reference implementation of the
`LogEventRepositoryProtocol`. If we change the Protocol surface, this
fake fails to type-check, and the production `LogEventRepository` will
too — which is exactly the structural-typing payoff we want.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from watchdog_core.domain.hashing import compute_message_hash
from watchdog_core.domain.models import LogEvent
from watchdog_core.services.ingestion_service import (
    DEDUPE_WINDOW,
    MAX_EVENT_AGE,
    EventRejectedError,
    IngestionService,
)


class FakeLogEventRepo:
    """In-memory Protocol implementation for IngestionService tests."""

    def __init__(self) -> None:
        self.events: list[LogEvent] = []

    async def insert(self, event: LogEvent) -> None:
        self.events.append(event)

    async def insert_many(self, events: list[LogEvent]) -> None:
        self.events.extend(events)

    async def find_duplicate(
        self,
        *,
        service: str,
        ts: datetime,
        message_hash: str,
        window: timedelta,
    ) -> LogEvent | None:
        for e in self.events:
            if e.service != service:
                continue
            if compute_message_hash(e.message) != message_hash:
                continue
            if abs((e.ts - ts).total_seconds()) > window.total_seconds():
                continue
            return e
        return None


_FIXED_NOW = datetime(2026, 5, 19, 12, 0, 0, tzinfo=UTC)


def _make_service() -> tuple[IngestionService, FakeLogEventRepo]:
    repo = FakeLogEventRepo()
    svc = IngestionService(repo, now=lambda: _FIXED_NOW)
    return svc, repo


# ---------------------------------------------------------------------------
# Happy path and normalization
# ---------------------------------------------------------------------------


async def test_ingest_happy_path_normalizes_service() -> None:
    svc, repo = _make_service()
    event = await svc.ingest(
        service="  Auth-API  ",
        level="ERROR",
        message="boom",
        ts=_FIXED_NOW - timedelta(seconds=1),
    )
    assert event is not None
    assert event.service == "auth-api"
    assert len(repo.events) == 1


async def test_ingest_uses_default_clock_when_none_passed() -> None:
    # Exercise the default-clock branch (Callable | None path).
    repo = FakeLogEventRepo()
    svc = IngestionService(repo)  # no `now=`
    event = await svc.ingest(
        service="api",
        level="INFO",
        message="hello",
        ts=datetime.now(UTC) - timedelta(seconds=1),
    )
    assert event is not None


# ---------------------------------------------------------------------------
# Rejections
# ---------------------------------------------------------------------------


async def test_ingest_rejects_naive_ts() -> None:
    svc, _ = _make_service()
    naive_ts = datetime(2026, 5, 19, 12, 0, 0)  # noqa: DTZ001 — naive on purpose
    with pytest.raises(EventRejectedError, match="timezone-aware"):
        await svc.ingest(service="api", level="ERROR", message="x", ts=naive_ts)


async def test_ingest_rejects_empty_service() -> None:
    svc, _ = _make_service()
    with pytest.raises(EventRejectedError, match="non-empty"):
        await svc.ingest(service="   ", level="ERROR", message="x", ts=_FIXED_NOW)


async def test_ingest_rejects_event_older_than_24h() -> None:
    svc, _ = _make_service()
    old_ts = _FIXED_NOW - timedelta(hours=25)
    with pytest.raises(EventRejectedError, match="old"):
        await svc.ingest(service="api", level="ERROR", message="x", ts=old_ts)


# ---------------------------------------------------------------------------
# Dedupe
# ---------------------------------------------------------------------------


async def test_ingest_dedupes_within_window() -> None:
    svc, repo = _make_service()
    base = _FIXED_NOW - timedelta(seconds=1)
    first = await svc.ingest(service="api", level="ERROR", message="boom", ts=base)
    second = await svc.ingest(
        service="api",
        level="ERROR",
        message="boom",
        ts=base + timedelta(seconds=3),
    )
    assert first is not None
    assert second is None
    assert len(repo.events) == 1


async def test_ingest_no_dedupe_outside_window() -> None:
    svc, repo = _make_service()
    base = _FIXED_NOW - timedelta(seconds=10)
    await svc.ingest(service="api", level="ERROR", message="x", ts=base)
    later = await svc.ingest(
        service="api",
        level="ERROR",
        message="x",
        ts=base + DEDUPE_WINDOW + timedelta(seconds=1),
    )
    assert later is not None
    assert len(repo.events) == 2


async def test_ingest_no_dedupe_across_services() -> None:
    svc, repo = _make_service()
    base = _FIXED_NOW - timedelta(seconds=1)
    await svc.ingest(service="api", level="ERROR", message="boom", ts=base)
    other = await svc.ingest(service="worker", level="ERROR", message="boom", ts=base)
    assert other is not None
    assert len(repo.events) == 2


# ---------------------------------------------------------------------------
# Property-based timestamp invariants
# ---------------------------------------------------------------------------

_RECENT_SECONDS = st.floats(min_value=1.0, max_value=MAX_EVENT_AGE.total_seconds() - 1.0)
_OLD_SECONDS = st.floats(
    min_value=MAX_EVENT_AGE.total_seconds() + 60.0,
    max_value=30 * 24 * 3600.0,
)


@given(seconds_old=_RECENT_SECONDS)
@settings(
    deadline=None,
    max_examples=25,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
async def test_property_events_within_max_age_are_accepted(seconds_old: float) -> None:
    svc, _ = _make_service()
    ts = _FIXED_NOW - timedelta(seconds=seconds_old)
    event = await svc.ingest(
        service="api",
        level="INFO",
        message=f"msg-{seconds_old}",
        ts=ts,
    )
    assert event is not None


@given(seconds_old=_OLD_SECONDS)
@settings(
    deadline=None,
    max_examples=25,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
async def test_property_events_beyond_max_age_are_rejected(seconds_old: float) -> None:
    svc, _ = _make_service()
    ts = _FIXED_NOW - timedelta(seconds=seconds_old)
    with pytest.raises(EventRejectedError, match="old"):
        await svc.ingest(service="api", level="INFO", message="x", ts=ts)
