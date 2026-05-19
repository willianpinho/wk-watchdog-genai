"""Custom Prometheus metrics for wk-watchdog.

All metrics live in the global `prometheus_client.REGISTRY` so the
`/metrics` endpoint (served by `watchdog_api.routes.metrics`) and the
OTel `PrometheusMetricReader` both see them.

Re-import safety
----------------
pytest can re-collect modules across test files, which causes
`prometheus_client.Counter(...)` to raise `ValueError: Duplicated
timeseries`. `_get_or_create` recovers the existing collector by
name so the module is safe to import many times. (Same pattern as
`watchdog_core.genai.cost_guard`.)
"""

from __future__ import annotations

from typing import Any, cast

from prometheus_client import REGISTRY, Counter, Gauge, Histogram

# Re-export the genai counter that was defined first in cost_guard.py so
# callers can `from watchdog_core.observability.metrics import GENAI_TOKENS`
# without learning the location of the cost-guard module.
from watchdog_core.genai.cost_guard import _TOKENS_COUNTER as GENAI_TOKENS


def _get_or_create(
    metric_cls: type,
    name: str,
    description: str,
    labelnames: list[str] | None = None,
    **kwargs: Any,
) -> Any:
    """Return a metric, re-using an existing collector if name collides."""
    try:
        if labelnames is None:
            return metric_cls(name, description, **kwargs)
        return metric_cls(name, description, labelnames, **kwargs)
    except ValueError:
        # prometheus_client has no public lookup-by-name; the only stable
        # path is through the private registry attribute.
        existing = list(REGISTRY._names_to_collectors.values())  # noqa: SLF001
        for collector in existing:
            if getattr(collector, "_name", None) == name:
                return cast("Any", collector)
        raise


EVENTS_INGESTED = _get_or_create(
    Counter,
    "events_ingested_total",
    "Number of log events successfully ingested.",
    ["service", "level"],
)

ANOMALIES_DETECTED = _get_or_create(
    Counter,
    "anomalies_detected_total",
    "Number of anomaly windows raised by the detector.",
    ["service", "level", "severity"],
)

# Webhook delivery latency histogram. Buckets per the brief:
# 50 ms → 10 s, biased toward the sub-second range that dashboards care
# about for synchronous delivery SLOs.
WEBHOOK_DELIVERY_LATENCY = _get_or_create(
    Histogram,
    "webhook_delivery_latency_seconds",
    "Latency of webhook dispatcher POSTs.",
    ["outcome"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)

OUTBOX_QUEUE_DEPTH = _get_or_create(
    Gauge,
    "outbox_queue_depth",
    "Pending rows in webhook_outbox (sampled by the worker).",
)


__all__ = [
    "ANOMALIES_DETECTED",
    "EVENTS_INGESTED",
    "GENAI_TOKENS",
    "OUTBOX_QUEUE_DEPTH",
    "WEBHOOK_DELIVERY_LATENCY",
]
