"""AnomalyDetector tests on synthetic timeseries."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from watchdog_core.detection.detector import AnomalyDetector
from watchdog_core.domain.models import LogEvent

_BASE = datetime(2026, 5, 19, 12, 0, 0, tzinfo=UTC)


def _event(ts: datetime, *, service: str = "api", level: str = "ERROR") -> LogEvent:
    return LogEvent(
        id=uuid4(),
        ts=ts,
        service=service,
        level=level,
        message=f"m-{ts.isoformat()}",
    )


def test_detector_steady_baseline_no_anomaly() -> None:
    """60 minutes at 3 events/min — nothing fires when the next minute opens."""
    detector = AnomalyDetector(threshold=3.0, min_floor=5)
    fired: list = []
    for minute in range(60):
        for second in (10, 30, 50):
            fired.extend(
                detector.observe(_event(_BASE + timedelta(minutes=minute, seconds=second))),
            )
    # Step into minute 60 — closes minute 59's bucket of 3 against a baseline of 3.
    fired.extend(detector.observe(_event(_BASE + timedelta(minutes=60))))
    assert fired == []


def test_detector_fires_on_spike() -> None:
    """60 min of 2/min baseline, then a 50-count spike → fires when the spike closes."""
    detector = AnomalyDetector(threshold=3.0, min_floor=5)
    for minute in range(60):
        for second in (10, 40):
            detector.observe(_event(_BASE + timedelta(minutes=minute, seconds=second)))
    spike_minute = _BASE + timedelta(minutes=60)
    for i in range(50):
        detector.observe(_event(spike_minute + timedelta(seconds=i)))
    closes = detector.observe(_event(_BASE + timedelta(minutes=61)))
    assert len(closes) == 1
    anomaly = closes[0]
    assert anomaly.service == "api"
    assert anomaly.level == "ERROR"
    assert anomaly.count == 50
    assert anomaly.window_start == spike_minute
    assert anomaly.window_end == _BASE + timedelta(minutes=61)
    assert anomaly.z_score > 3.0


def test_detector_silent_when_count_below_floor() -> None:
    """High z but count < min_floor → silent (avoid alerting on tiny baselines)."""
    detector = AnomalyDetector(threshold=3.0, min_floor=5)
    # 60 minutes of zero events (initialize with one then gap-roll).
    detector.observe(_event(_BASE))
    # Step way forward; gap-rollover fills baseline with zero buckets.
    detector.observe(_event(_BASE + timedelta(minutes=60)))
    # Now in minute 60 with count=1 already. Add 3 more → total count=4,
    # which is below floor=5 even though z would be very high.
    for i in range(3):
        detector.observe(_event(_BASE + timedelta(minutes=60, seconds=i + 1)))
    finals = detector.observe(_event(_BASE + timedelta(minutes=61)))
    assert finals == []


def test_detector_separate_keys_have_independent_state() -> None:
    """A spike on `worker` must not affect `api`'s baseline."""
    detector = AnomalyDetector(threshold=3.0, min_floor=5)
    for minute in range(60):
        for second in (10, 40):
            detector.observe(
                _event(_BASE + timedelta(minutes=minute, seconds=second), service="api")
            )
            detector.observe(
                _event(_BASE + timedelta(minutes=minute, seconds=second), service="worker")
            )
    spike = _BASE + timedelta(minutes=60)
    for i in range(50):
        detector.observe(_event(spike + timedelta(seconds=i), service="worker"))
    api_close = detector.observe(_event(_BASE + timedelta(minutes=61), service="api"))
    worker_close = detector.observe(_event(_BASE + timedelta(minutes=61), service="worker"))
    assert api_close == []
    assert len(worker_close) == 1
    assert worker_close[0].service == "worker"


def test_detector_ignores_past_event() -> None:
    """Late-arriving event (ts before current minute) is silently dropped."""
    detector = AnomalyDetector(threshold=3.0, min_floor=5)
    detector.observe(_event(_BASE + timedelta(minutes=5)))
    # Late event from minute 2
    fired = detector.observe(_event(_BASE + timedelta(minutes=2)))
    assert fired == []
