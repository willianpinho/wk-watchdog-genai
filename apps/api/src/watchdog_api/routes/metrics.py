"""Prometheus exposition endpoint.

Serves the global `prometheus_client` registry as the standard
text-format response that Prometheus / Grafana Agent / OTel
Collector all understand. Both our custom prometheus_client metrics
(events_ingested_total, anomalies_detected_total,
webhook_delivery_latency_seconds, genai_tokens_total,
outbox_queue_depth) and the OTel `PrometheusMetricReader`-bridged
auto-instrumentation metrics surface through the same endpoint.
"""

from __future__ import annotations

from fastapi import APIRouter, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

# Importing the metrics module triggers metric registration so the
# /metrics output always includes our custom series even if the
# routes that increment them haven't been hit yet.
from watchdog_core.observability import metrics as _metrics  # noqa: F401 — side-effect import

router = APIRouter(tags=["observability"])


@router.get("/metrics", include_in_schema=False)
def metrics_endpoint() -> Response:
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
