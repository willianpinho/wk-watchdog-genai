"""End-to-end alerting test.

Exercises: AlertService.create_and_enqueue → outbox row → OutboxWorker
tick → WebhookDispatcher signs and POSTs via ASGITransport → sink route
verifies HMAC and records the payload.

The detector is NOT wired into the ingestion request path yet (deferred
from Turn 4 with explicit scoping in prompts.md); this test exercises
the alerting pipeline FROM the AlertService onward, which is what Turn 6
is responsible for. The ingest→detect→alert wiring is Turn 7+.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import aiosqlite
import httpx
import pytest_asyncio
from httpx import ASGITransport

from watchdog_api.config import Settings
from watchdog_api.main import create_app
from watchdog_core.alerting.outbox_worker import OutboxWorker
from watchdog_core.alerting.webhook_dispatcher import WebhookDispatcher
from watchdog_core.domain.models import Alert, AnomalyWindow
from watchdog_core.genai.severity_classifier import SeverityDecision
from watchdog_core.persistence.migrations import apply_migrations
from watchdog_core.persistence.repositories import AlertRepository, OutboxRepository
from watchdog_core.services.alert_service import AlertService

_SECRET = "e2e-test-secret"  # noqa: S105 — test fixture, not a real credential


class _StubClassifier:
    async def classify(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
        return SeverityDecision(
            severity="critical",
            reasoning="stub e2e",
            confidence=0.95,
            suggested_action="page on-call",
            model="stub",
            latency_ms=1,
        )


@pytest_asyncio.fixture
async def app_with_sink(tmp_path: Path):
    db_path = tmp_path / "e2e.sqlite"
    settings = Settings(
        db_url=f"sqlite+aiosqlite:///{db_path}",
        webhook_target_url="",  # do NOT start the lifespan worker; we drive it manually
        webhook_secret=_SECRET,
    )
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        yield app, str(db_path)


async def test_e2e_alert_dispatched_signed_and_received(app_with_sink) -> None:  # type: ignore[no-untyped-def]
    app, db_path = app_with_sink

    # Step 1: create an Alert + outbox row via the atomic service path
    # (this stands in for "anomaly detected in ingestion" — see test
    # docstring for the deferred wiring).
    alert = Alert(
        id=uuid4(),
        anomaly=AnomalyWindow(
            service="payments",
            level="ERROR",
            window_start=datetime(2026, 5, 19, 12, 0, 0, tzinfo=UTC),
            window_end=datetime(2026, 5, 19, 12, 1, 0, tzinfo=UTC),
            count=80,
            baseline_mean=4.0,
            baseline_stddev=2.0,
            z_score=38.0,
        ),
        severity="critical",
        reasoning="payment failures spike",
        created_at=datetime.now(UTC),
    )

    async with aiosqlite.connect(db_path) as conn:
        await apply_migrations(conn)
        alert_repo = AlertRepository(conn)
        outbox_repo = OutboxRepository(conn)
        service = AlertService(
            uow=conn,
            alert_repo=alert_repo,
            outbox_repo=outbox_repo,
            classifier=_StubClassifier(),
        )
        await service.create_and_enqueue(
            alert,
            {
                "alert_id": str(alert.id),
                "service": alert.anomaly.service,
                "severity": alert.severity,
            },
        )

        # Step 2: run the worker once against the SAME app via ASGI
        # transport (so the dispatcher's POST reaches /v1/_sink).
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            dispatcher = WebhookDispatcher(client=client)
            try:
                worker = OutboxWorker(
                    dispatcher=dispatcher,
                    target_url="http://testserver/v1/_sink",
                    secret=_SECRET,
                    conn=conn,
                    backoff_seconds=(0.0, 0.0, 0.0, 0.0),
                )
                handled = await worker.tick(outbox_repo, alert_repo)
                assert handled == 1
            finally:
                # Don't close the injected client; the AsyncClient ctx does that.
                pass

    # Step 3: verify the sink received the payload AND it passed HMAC verification.
    received = getattr(app.state, "sink_received", [])
    assert len(received) == 1, f"sink_received: {received}"
    payload = received[0]
    assert payload["alert_id"] == str(alert.id)
    assert payload["severity"] == "critical"
    assert payload["service"] == "payments"
