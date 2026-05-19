"""AlertService integration test with in-memory Protocol fakes."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from watchdog_core.domain.models import Alert, AnomalyWindow
from watchdog_core.genai.severity_classifier import SeverityDecision
from watchdog_core.services.alert_service import AlertService


class _FakeAlertRepo:
    def __init__(self) -> None:
        self.alerts: list[Alert] = []

    async def insert(self, alert: Alert) -> None:
        self.alerts.append(alert)


class _FakeOutbox:
    def __init__(self) -> None:
        self.calls: list[tuple[UUID, dict[str, Any]]] = []

    async def enqueue(self, alert_id: UUID, payload: dict[str, Any]) -> int:
        self.calls.append((alert_id, payload))
        return len(self.calls)


class _StubClassifier:
    async def classify(
        self,
        anomaly: AnomalyWindow,
        recent_messages: list[str],
    ) -> SeverityDecision:
        _ = recent_messages
        return SeverityDecision(
            severity="high" if anomaly.z_score > 10 else "low",
            reasoning="stub reasoning",
            confidence=0.9,
            suggested_action="stub action",
            model="stub-classifier",
            latency_ms=1,
        )


async def test_alert_service_handles_anomaly_persists_and_enqueues() -> None:
    repo = _FakeAlertRepo()
    outbox = _FakeOutbox()
    svc = AlertService(
        alert_repo=repo,
        outbox_repo=outbox,
        classifier=_StubClassifier(),
    )
    anomaly = AnomalyWindow(
        service="api",
        level="ERROR",
        window_start=datetime(2026, 5, 19, 12, 0, 0, tzinfo=UTC),
        window_end=datetime(2026, 5, 19, 12, 1, 0, tzinfo=UTC),
        count=80,
        baseline_mean=4.0,
        baseline_stddev=2.0,
        z_score=38.0,
    )

    async def messages_provider(service: str, level: str) -> list[str]:
        _ = (service, level)
        return ["err A", "err B"]

    alerts = await svc.handle_anomalies([anomaly], messages_provider)

    assert len(alerts) == 1
    assert alerts[0].severity == "high"
    assert len(repo.alerts) == 1
    assert len(outbox.calls) == 1
    payload = outbox.calls[0][1]
    assert payload["service"] == "api"
    assert payload["severity"] == "high"
    assert payload["model"] == "stub-classifier"


async def test_alert_service_no_anomalies_is_noop() -> None:
    repo = _FakeAlertRepo()
    outbox = _FakeOutbox()
    svc = AlertService(
        alert_repo=repo,
        outbox_repo=outbox,
        classifier=_StubClassifier(),
    )

    async def messages_provider(service: str, level: str) -> list[str]:
        _ = (service, level)
        return []

    alerts = await svc.handle_anomalies([], messages_provider)
    assert alerts == []
    assert repo.alerts == []
    assert outbox.calls == []
