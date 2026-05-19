"""Anomaly detection orchestrator (per-key minute buckets).

State is held in-process (`self._state: dict`). For the MVP, a single
uvicorn worker maintains coherent per-key baselines and process
restart is rare enough that the ~1 h EWMA warm-up cost is acceptable.

Migrating to horizontal scaling
-------------------------------
When the API outgrows one worker, in-process state goes inconsistent
across replicas (each replica sees only a fraction of events;
baselines diverge per replica). Mitigations, in increasing order of
operational cost:

  1. Pin per-key traffic to one worker via consistent-hash routing.
     Cheap; ties workers to keys; complicates blue/green deploys.
  2. Move per-key BucketState into Redis (HSET keyed by
     `watchdog:baseline:{service}:{level}` holding
     `current_minute`, `current_count`, `ewma_mean`,
     `welford_m2`, `n`). Each `observe()` becomes a Redis
     transaction (WATCH/MULTI or a Lua script) to keep the
     read-modify-write atomic.
  3. Adopt a streaming engine (Bytewax / Kafka Streams / Flink)
     where keyed state and exactly-once windowing are first-class.

This module commits to none of those now. ADR-005 (TBD) will record
the choice when traffic volume justifies the cost.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from watchdog_core.detection.baseline import EWMABaseline
from watchdog_core.domain.models import AnomalyWindow, LogEvent

_BUCKET_WIDTH = timedelta(minutes=1)
_DEFAULT_THRESHOLD = 3.0
_DEFAULT_FLOOR = 5


def _floor_minute(ts: datetime) -> datetime:
    return ts.replace(second=0, microsecond=0)


@dataclass
class _BucketState:
    """Per-(service, level) detection state."""

    current_minute: datetime
    current_count: int
    baseline: EWMABaseline


class AnomalyDetector:
    """Detects spikes in event counts per (service, level).

    Algorithm
    ---------
    On each observed event:
      * Floor the event ts to a minute boundary.
      * If still inside the current minute, increment count.
      * If we've crossed into a later minute:
          - Close the previous bucket: compute
              z = (count - mean) / max(stddev, 1.0)
            Fire an `AnomalyWindow` if `z > threshold` AND
            `count >= min_floor`.
          - Roll any missing minutes through the baseline with
            count = 0, so an idle period biases the baseline *down*,
            not up.
          - Reset state to the new minute, count = 1.
      * Past events (event_minute < current_minute) are ignored;
        late arrivals are out of scope for the MVP detector.

    The `max(stddev, 1.0)` floor in the denominator prevents synthetic
    z-explosions when the baseline has near-zero variance.
    """

    def __init__(
        self,
        *,
        threshold: float = _DEFAULT_THRESHOLD,
        min_floor: int = _DEFAULT_FLOOR,
    ) -> None:
        self._threshold = threshold
        self._floor = min_floor
        self._state: dict[tuple[str, str], _BucketState] = {}

    def observe(self, event: LogEvent) -> list[AnomalyWindow]:
        key = (event.service, event.level)
        event_minute = _floor_minute(event.ts)
        state = self._state.get(key)

        if state is None:
            self._state[key] = _BucketState(
                current_minute=event_minute,
                current_count=1,
                baseline=EWMABaseline(),
            )
            return []

        if event_minute == state.current_minute:
            state.current_count += 1
            return []

        if event_minute < state.current_minute:
            # Late event — out of scope for MVP.
            return []

        # event_minute > current_minute: cross at least one boundary.
        anomalies = self._close_current_bucket(event.service, event.level, state)

        # Fold any gap minutes (no observed events) as zero buckets.
        gap = state.current_minute + _BUCKET_WIDTH
        while gap < event_minute:
            state.baseline.update(0)
            gap += _BUCKET_WIDTH

        state.current_minute = event_minute
        state.current_count = 1
        return anomalies

    def _close_current_bucket(
        self,
        service: str,
        level: str,
        state: _BucketState,
    ) -> list[AnomalyWindow]:
        anomalies: list[AnomalyWindow] = []
        mean, stddev = state.baseline.current()
        z = (state.current_count - mean) / max(stddev, 1.0)
        if z > self._threshold and state.current_count >= self._floor:
            anomalies.append(
                AnomalyWindow(
                    service=service,
                    level=level,
                    window_start=state.current_minute,
                    window_end=state.current_minute + _BUCKET_WIDTH,
                    count=state.current_count,
                    baseline_mean=mean,
                    baseline_stddev=stddev,
                    z_score=z,
                ),
            )
        state.baseline.update(state.current_count)
        return anomalies
