"""Alert pipeline: anomaly window → severity decision → persisted Alert + outbox row.

This service is the integration seam called by the orchestrator (the
ingestion route does NOT call the classifier directly — that
separation keeps the request hot path predictable and lets us push
the classifier behind a queue when latency matters).

The repositories and classifier are injected via Protocols, so this
module imports neither aiosqlite nor anthropic — the same three-layer
discipline as IngestionService.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol
from uuid import UUID, uuid4

from watchdog_core.domain.models import Alert

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from watchdog_core.domain.models import AnomalyWindow
    from watchdog_core.genai.severity_classifier import SeverityDecision


class AlertRepositoryProtocol(Protocol):
    async def insert(self, alert: Alert) -> None: ...


class OutboxRepositoryProtocol(Protocol):
    async def enqueue(self, alert_id: UUID, payload: dict[str, Any]) -> int: ...


class SeverityClassifierProtocol(Protocol):
    async def classify(
        self,
        anomaly: AnomalyWindow,
        recent_messages: list[str],
    ) -> SeverityDecision: ...


class AlertService:
    """Owns the lifecycle anomaly → classified → persisted → queued."""

    def __init__(
        self,
        *,
        alert_repo: AlertRepositoryProtocol,
        outbox_repo: OutboxRepositoryProtocol,
        classifier: SeverityClassifierProtocol,
    ) -> None:
        self._alerts = alert_repo
        self._outbox = outbox_repo
        self._classifier = classifier

    async def handle_anomalies(
        self,
        anomalies: list[AnomalyWindow],
        recent_messages_provider: Callable[[str, str], Awaitable[list[str]]],
    ) -> list[Alert]:
        """For each anomaly: classify, persist Alert, enqueue webhook job."""
        alerts: list[Alert] = []
        for anomaly in anomalies:
            recent = await recent_messages_provider(anomaly.service, anomaly.level)
            decision = await self._classifier.classify(anomaly, recent)
            alert = Alert(
                id=uuid4(),
                anomaly=anomaly,
                severity=decision.severity,
                reasoning=decision.reasoning,
                created_at=datetime.now(UTC),
            )
            await self._alerts.insert(alert)
            await self._outbox.enqueue(
                alert.id,
                {
                    "alert_id": str(alert.id),
                    "service": anomaly.service,
                    "level": anomaly.level,
                    "severity": decision.severity,
                    "reasoning": decision.reasoning,
                    "suggested_action": decision.suggested_action,
                    "model": decision.model,
                    "confidence": decision.confidence,
                },
            )
            alerts.append(alert)
        return alerts
