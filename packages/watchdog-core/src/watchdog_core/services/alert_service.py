"""Alert pipeline: anomaly window → severity decision → persisted Alert + outbox row.

This service is the integration seam called by the orchestrator (the
ingestion route does NOT call the classifier directly — that
separation keeps the request hot path predictable and lets us push
the classifier behind a queue when latency matters).

The repositories and classifier are injected via Protocols, so this
module imports neither aiosqlite nor anthropic — the same three-layer
discipline as IngestionService.

Atomicity (Turn 6)
------------------
`create_and_enqueue` is the implementation of the *real* transactional
outbox pattern: it writes the `alerts` row AND the `webhook_outbox`
row in a single transaction. If either insert raises, the
`UnitOfWorkProtocol.rollback()` reverses both. This eliminates the
dual-write race where a crash between the two writes would leave an
alert in the DB with no dispatcher job to deliver it.
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
    async def insert(self, alert: Alert, *, commit: bool = True) -> None: ...


class OutboxRepositoryProtocol(Protocol):
    async def enqueue(
        self,
        alert_id: UUID,
        payload: dict[str, Any],
        *,
        commit: bool = True,
    ) -> int: ...


class SeverityClassifierProtocol(Protocol):
    async def classify(
        self,
        anomaly: AnomalyWindow,
        recent_messages: list[str],
    ) -> SeverityDecision: ...


class UnitOfWorkProtocol(Protocol):
    """Minimal transaction surface — `aiosqlite.Connection` satisfies it.

    AlertService does not import aiosqlite directly; this Protocol is
    what `create_and_enqueue` calls into to commit / rollback the
    combined alert+outbox write.
    """

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...


class AlertService:
    """Owns the lifecycle anomaly → classified → persisted → queued."""

    def __init__(
        self,
        *,
        uow: UnitOfWorkProtocol,
        alert_repo: AlertRepositoryProtocol,
        outbox_repo: OutboxRepositoryProtocol,
        classifier: SeverityClassifierProtocol,
    ) -> None:
        self._uow = uow
        self._alerts = alert_repo
        self._outbox = outbox_repo
        self._classifier = classifier

    # ------------------------------------------------------------------
    # Atomic single-alert path (Turn 6)
    # ------------------------------------------------------------------

    async def create_and_enqueue(
        self,
        alert: Alert,
        payload: dict[str, Any],
    ) -> None:
        """Atomically write `alert` AND the matching `webhook_outbox` row.

        If either statement raises, both are rolled back via
        `uow.rollback()`. This is the actual transactional outbox: a
        crash between the two writes would, in dual-write code, leave
        an alert persisted with no dispatcher job to deliver it. With
        this method, that crash leaves the DB consistent and the next
        ingestion retry will re-create both rows together.
        """
        try:
            await self._alerts.insert(alert, commit=False)
            await self._outbox.enqueue(alert.id, payload, commit=False)
            await self._uow.commit()
        except Exception:
            await self._uow.rollback()
            raise

    # ------------------------------------------------------------------
    # Anomaly batch path (Turn 5; now delegates to create_and_enqueue
    # for atomicity).
    # ------------------------------------------------------------------

    async def handle_anomalies(
        self,
        anomalies: list[AnomalyWindow],
        recent_messages_provider: Callable[[str, str], Awaitable[list[str]]],
    ) -> list[Alert]:
        """For each anomaly: classify, atomically persist Alert + outbox row."""
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
            payload: dict[str, Any] = {
                "alert_id": str(alert.id),
                "service": anomaly.service,
                "level": anomaly.level,
                "severity": decision.severity,
                "reasoning": decision.reasoning,
                "suggested_action": decision.suggested_action,
                "model": decision.model,
                "confidence": decision.confidence,
            }
            await self.create_and_enqueue(alert, payload)
            alerts.append(alert)
        return alerts
