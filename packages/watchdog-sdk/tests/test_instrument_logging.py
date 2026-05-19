"""Instrumentation tests — verify the WatchdogHandler buffers + flushes."""

from __future__ import annotations

import logging
import time
from typing import Any

import pytest

from watchdog_sdk import LogEventInput, instrument_logging
from watchdog_sdk.client import EventAck


class _FakeClient:
    """Stand-in for WatchdogClient that records every send_batch call."""

    def __init__(self) -> None:
        self.batches: list[list[LogEventInput]] = []

    def send_batch(self, events: list[LogEventInput]) -> EventAck:
        self.batches.append(list(events))
        return EventAck(accepted=len(events), rejected=[])

    # The real client also exposes send_event; the handler only uses
    # send_batch, so we leave a no-op stub for type-completeness.
    def send_event(self, event: LogEventInput) -> EventAck:  # pragma: no cover
        _ = event
        msg = "FakeClient does not implement send_event"
        raise NotImplementedError(msg)


def test_handler_flushes_when_batch_size_reached() -> None:
    fake: Any = _FakeClient()  # `Any` because the handler asks for the concrete type
    logger = logging.getLogger("test-handler-size")
    logger.setLevel(logging.DEBUG)
    handler = instrument_logging(
        logger,
        client=fake,
        service="test-svc",
        batch_size=10,
        flush_interval_s=60.0,  # high so the test isn't time-dependent
    )
    try:
        for i in range(10):
            logger.error("err-%d", i)
        # 10 records, batch_size=10 → one flush triggered.
        assert len(fake.batches) == 1
        assert len(fake.batches[0]) == 10
        assert fake.batches[0][0].message == "err-0"
        assert fake.batches[0][-1].message == "err-9"
    finally:
        handler.close()
        logger.removeHandler(handler)


def test_handler_flushes_on_explicit_flush_call() -> None:
    fake: Any = _FakeClient()
    logger = logging.getLogger("test-handler-explicit-flush")
    logger.setLevel(logging.DEBUG)
    handler = instrument_logging(
        logger,
        client=fake,
        service="test-svc",
        batch_size=100,
        flush_interval_s=60.0,
    )
    try:
        for i in range(5):
            logger.info("info-%d", i)
        # Buffer below batch_size; no auto-flush yet.
        assert fake.batches == []
        handler.flush()
        assert len(fake.batches) == 1
        assert len(fake.batches[0]) == 5
    finally:
        handler.close()
        logger.removeHandler(handler)


def test_handler_maps_stdlib_level_names_to_watchdog_levels() -> None:
    fake: Any = _FakeClient()
    logger = logging.getLogger("test-handler-level-map")
    logger.setLevel(logging.DEBUG)
    handler = instrument_logging(
        logger,
        client=fake,
        service="test-svc",
        batch_size=100,
        flush_interval_s=60.0,
    )
    try:
        logger.debug("d")
        logger.info("i")
        logger.warning("w")
        logger.error("e")
        logger.critical("c")
        handler.flush()
        levels = [event.level for event in fake.batches[0]]
        # stdlib WARNING → watchdog WARN; everything else is 1:1.
        assert levels == ["DEBUG", "INFO", "WARN", "ERROR", "CRITICAL"]
    finally:
        handler.close()
        logger.removeHandler(handler)


@pytest.mark.slow
def test_handler_periodic_flush_thread_fires() -> None:
    """The background thread flushes on `flush_interval_s` even without
    a size trigger. Sleep just over one interval and assert."""
    fake: Any = _FakeClient()
    logger = logging.getLogger("test-handler-periodic")
    logger.setLevel(logging.DEBUG)
    handler = instrument_logging(
        logger,
        client=fake,
        service="test-svc",
        batch_size=100,
        flush_interval_s=0.2,
    )
    try:
        logger.info("one record")
        time.sleep(0.5)  # > 2x the flush interval — should have fired
        assert len(fake.batches) >= 1
        assert fake.batches[0][0].message == "one record"
    finally:
        handler.close()
        logger.removeHandler(handler)
