"""Test data builders — DAMP > DRY for test code.

Tests are read FAR more often than they're written, and a test that
spells out its inputs at the call site (Descriptive And Meaningful
Phrases) is easier to debug than one that hides them behind a shared
fixture or factory parameterisation (Don't Repeat Yourself).

We therefore export `make_log_event`, `make_anomaly`, and `make_alert`
that accept every domain field as a keyword override; tests can write:

    event = make_log_event(service="payments", level="CRITICAL", count=200)

and the reader knows EVERY field that's load-bearing for the test just
by reading the call. Defaults are sane and explicit (no random UUIDs
embedded in fixtures).

Reference: Khorikov 2020, *Unit Testing Principles, Practices, and
Patterns*, Ch. 4 — "Express what each test is doing as a story; favour
clarity over deduplication."
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from watchdog_core.domain.models import (
    Alert,
    AnomalyWindow,
    LogEvent,
)

_T = datetime(2026, 5, 19, 12, 0, 0, tzinfo=UTC)


def make_log_event(**overrides: Any) -> LogEvent:
    """Build a `LogEvent` with sensible defaults; pass overrides by kwarg."""
    defaults: dict[str, Any] = {
        "id": uuid4(),
        "ts": _T,
        "service": "test-service",
        "level": "ERROR",
        "message": "test message",
        "attributes": {},
    }
    defaults.update(overrides)
    return LogEvent(**defaults)


def make_anomaly(**overrides: Any) -> AnomalyWindow:
    defaults: dict[str, Any] = {
        "service": "test-service",
        "level": "ERROR",
        "window_start": _T,
        "window_end": _T + timedelta(minutes=1),
        "count": 50,
        "baseline_mean": 5.0,
        "baseline_stddev": 2.0,
        "z_score": 22.5,
    }
    defaults.update(overrides)
    return AnomalyWindow(**defaults)


def make_alert(**overrides: Any) -> Alert:
    defaults: dict[str, Any] = {
        "id": uuid4(),
        "anomaly": make_anomaly(),
        "severity": "high",
        "reasoning": "test reasoning",
        "created_at": _T,
    }
    defaults.update(overrides)
    return Alert(**defaults)


__all__ = ["make_alert", "make_anomaly", "make_log_event"]
