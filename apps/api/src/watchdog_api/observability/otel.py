"""OpenTelemetry configuration for wk-watchdog.

`configure_otel(settings, app, *, span_processor=None)` wires up:

  * A `TracerProvider` with the canonical resource attributes
    (`service.name=watchdog-api`, `service.version=__version__`,
    `deployment.environment=settings.env`).
  * An OTLP-HTTP span exporter (configurable endpoint; defaults to
    `http://localhost:4318`). Tests inject a `SimpleSpanProcessor`
    backed by `InMemorySpanExporter` instead.
  * `FastAPIInstrumentor.instrument_app(app)` for HTTP server spans.
  * `HTTPXClientInstrumentor().instrument()` for outbound webhook
    spans AND automatic `traceparent` injection (so the receiver
    can correlate).
  * A `MeterProvider` with `PrometheusMetricReader`, bridging OTel
    instruments into the global `prometheus_client.REGISTRY` that
    `GET /metrics` serves.

Re-entry safety: instrumentor calls are idempotent across tests because
each test builds a fresh FastAPI app; the global `TracerProvider`
is replaced rather than appended, which is the OTel SDK's documented
behavior.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.exporter.prometheus import PrometheusMetricReader
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from watchdog_api import __version__

if TYPE_CHECKING:
    from fastapi import FastAPI

    from watchdog_api.config import Settings

_SERVICE_NAME = "watchdog-api"
_DEFAULT_OTLP_ENDPOINT = "http://localhost:4318/v1/traces"

_httpx_instrumented = False  # process-level guard


def configure_otel(
    settings: Settings,
    app: FastAPI,
    *,
    span_processor: SpanProcessor | None = None,
) -> TracerProvider:
    """Build and install the global TracerProvider + MeterProvider.

    Returns the `TracerProvider` so callers can hold a reference
    (useful for tests that want to flush spans).
    """
    resource = Resource.create(
        {
            "service.name": _SERVICE_NAME,
            "service.version": __version__,
            "deployment.environment": settings.env,
        },
    )

    provider = TracerProvider(resource=resource)
    if span_processor is not None:
        provider.add_span_processor(span_processor)
    else:
        endpoint = settings.otel_exporter_otlp_endpoint or _DEFAULT_OTLP_ENDPOINT
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
    trace.set_tracer_provider(provider)

    # FastAPIInstrumentor is per-app, safe across multiple apps.
    FastAPIInstrumentor.instrument_app(app)

    # HTTPXClientInstrumentor is global; calling .instrument() twice
    # raises. Guard for re-entry across multiple tests.
    global _httpx_instrumented  # noqa: PLW0603 — process-level guard
    if not _httpx_instrumented:
        HTTPXClientInstrumentor().instrument()
        _httpx_instrumented = True

    # MeterProvider with Prometheus reader → metrics flow into the
    # global prometheus_client.REGISTRY served by /metrics.
    reader = PrometheusMetricReader()
    metrics.set_meter_provider(MeterProvider(resource=resource, metric_readers=[reader]))

    return provider
