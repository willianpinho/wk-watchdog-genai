"""SQLite-backed repositories for wk-watchdog.

Pure persistence. No business rules; no HTTP errors. Every SQL statement
is parameterized — there is NO f-string interpolation of user data into
SQL. The companion test `test_repositories_use_parameterized_queries`
greps this module to enforce that invariant mechanically.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import UUID

from opentelemetry import trace

from watchdog_core.domain.hashing import compute_message_hash
from watchdog_core.domain.models import Alert, AnomalyWindow, LogEvent

_tracer = trace.get_tracer(__name__)

if TYPE_CHECKING:
    import aiosqlite


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _iso_opt(value: datetime | None) -> str | None:
    return _iso(value) if value is not None else None


class LogEventRepository:
    """Persistence for LogEvent records."""

    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def insert(self, event: LogEvent, *, commit: bool = True) -> None:
        """Insert a single LogEvent.

        Set `commit=False` to compose with `insert_many` or another
        statement inside a single transaction.
        """
        await self._conn.execute(
            "INSERT INTO log_events"
            "(id, ts, service, level, message, message_hash, attributes_json)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                str(event.id),
                _iso(event.ts),
                event.service,
                event.level,
                event.message,
                compute_message_hash(event.message),
                json.dumps(event.attributes, separators=(",", ":"), sort_keys=True),
            ),
        )
        if commit:
            await self._conn.commit()

    async def insert_many(self, events: list[LogEvent]) -> None:
        """Insert N events in ONE transaction, under a single OTel span.

        Span name `LogEventRepository.insert_many` is asserted by the
        observability smoke test (Turn 7).
        """
        with _tracer.start_as_current_span("LogEventRepository.insert_many") as span:
            span.set_attribute("batch.size", len(events))
            for event in events:
                await self.insert(event, commit=False)
            await self._conn.commit()

    async def rate_per_minute(self, *, minutes: int = 60) -> dict[str, list[int]]:
        """Per-minute event counts for the last `minutes` minutes.

        Returns uPlot-ready columnar arrays:
            {"ts": [unix_ts, ...], "counts": [int, ...]}
        with one entry per minute (zero-filled where no events were
        observed) so the chart shows a continuous timeline.
        """
        end = datetime.now(UTC).replace(second=0, microsecond=0)
        start = end - timedelta(minutes=minutes)
        cursor = await self._conn.execute(
            "SELECT strftime('%Y-%m-%dT%H:%M:00+00:00', ts) AS minute, COUNT(*) "
            "FROM log_events WHERE ts >= ? AND ts < ? "
            "GROUP BY minute",
            (_iso(start), _iso(end)),
        )
        rows = await cursor.fetchall()
        await cursor.close()

        minute_to_count = {row[0]: int(row[1]) for row in rows}
        ts_series: list[int] = []
        count_series: list[int] = []
        cur = start
        while cur < end:
            ts_series.append(int(cur.timestamp()))
            count_series.append(minute_to_count.get(_iso(cur), 0))
            cur += timedelta(minutes=1)
        return {"ts": ts_series, "counts": count_series}

    async def summary_last_24h(
        self,
        *,
        top_n_services: int = 5,
    ) -> dict[str, Any]:
        """Total event count + top N noisiest services in the last 24 h."""
        end = datetime.now(UTC)
        start = end - timedelta(hours=24)

        cur1 = await self._conn.execute(
            "SELECT COUNT(*) FROM log_events WHERE ts >= ?",
            (_iso(start),),
        )
        row = await cur1.fetchone()
        await cur1.close()
        total = int(row[0]) if row is not None else 0

        cur2 = await self._conn.execute(
            "SELECT service, COUNT(*) AS n FROM log_events "
            "WHERE ts >= ? GROUP BY service ORDER BY n DESC LIMIT ?",
            (_iso(start), top_n_services),
        )
        top = [(r[0], int(r[1])) for r in await cur2.fetchall()]
        await cur2.close()

        return {"total_events": total, "top_services": top}

    async def list_recent(self, service: str, *, limit: int = 100) -> list[LogEvent]:
        cursor = await self._conn.execute(
            "SELECT id, ts, service, level, message, attributes_json"
            " FROM log_events WHERE service = ?"
            " ORDER BY ts DESC LIMIT ?",
            (service, limit),
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return [self._row_to_event(r) for r in rows]

    async def count_in_window(
        self,
        service: str,
        level: str,
        *,
        window_start: datetime,
        window_end: datetime,
    ) -> int:
        cursor = await self._conn.execute(
            "SELECT COUNT(*) FROM log_events"
            " WHERE service = ? AND level = ?"
            " AND ts >= ? AND ts < ?",
            (service, level, _iso(window_start), _iso(window_end)),
        )
        row = await cursor.fetchone()
        await cursor.close()
        if row is None:
            return 0
        return int(row[0])

    async def find_duplicate(
        self,
        *,
        service: str,
        ts: datetime,
        message_hash: str,
        window: timedelta,
    ) -> LogEvent | None:
        ts_low = _iso(ts - window)
        ts_high = _iso(ts + window)
        cursor = await self._conn.execute(
            "SELECT id, ts, service, level, message, attributes_json"
            " FROM log_events"
            " WHERE service = ? AND message_hash = ?"
            " AND ts BETWEEN ? AND ?"
            " LIMIT 1",
            (service, message_hash, ts_low, ts_high),
        )
        row = await cursor.fetchone()
        await cursor.close()
        return self._row_to_event(row) if row is not None else None

    @staticmethod
    def _row_to_event(row: Any) -> LogEvent:
        return LogEvent(
            id=UUID(row[0]),
            ts=datetime.fromisoformat(row[1]),
            service=row[2],
            level=row[3],
            message=row[4],
            attributes=json.loads(row[5]),
        )


class AlertRepository:
    """Persistence for Alert records."""

    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def insert(self, alert: Alert, *, commit: bool = True) -> None:
        """Insert an alert.

        Set `commit=False` to compose with another statement inside
        a single transaction (used by `AlertService.create_and_enqueue`
        for the outbox atomicity).
        """
        await self._conn.execute(
            "INSERT INTO alerts ("
            "  id, service, level, window_start, window_end, event_count,"
            "  baseline_mean, baseline_stddev, z_score, severity, reasoning,"
            "  created_at, dispatched_at, webhook_status"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                str(alert.id),
                alert.anomaly.service,
                alert.anomaly.level,
                _iso(alert.anomaly.window_start),
                _iso(alert.anomaly.window_end),
                alert.anomaly.count,
                alert.anomaly.baseline_mean,
                alert.anomaly.baseline_stddev,
                alert.anomaly.z_score,
                alert.severity,
                alert.reasoning,
                _iso(alert.created_at),
                _iso_opt(alert.dispatched_at),
                alert.webhook_status,
            ),
        )
        if commit:
            await self._conn.commit()

    async def get_by_id(self, alert_id: UUID) -> Alert | None:
        """Lookup an alert by primary key. Returns None if absent."""
        cursor = await self._conn.execute(
            "SELECT id, service, level, window_start, window_end, event_count,"
            " baseline_mean, baseline_stddev, z_score, severity, reasoning,"
            " created_at, dispatched_at, webhook_status"
            " FROM alerts WHERE id = ?",
            (str(alert_id),),
        )
        row = await cursor.fetchone()
        await cursor.close()
        return self._row_to_alert(row) if row is not None else None

    async def list_recent(self, *, limit: int = 20) -> list[Alert]:
        """Recent alerts ordered by `created_at DESC`, ANY webhook_status."""
        cursor = await self._conn.execute(
            "SELECT id, service, level, window_start, window_end, event_count,"
            " baseline_mean, baseline_stddev, z_score, severity, reasoning,"
            " created_at, dispatched_at, webhook_status"
            " FROM alerts ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return [self._row_to_alert(r) for r in rows]

    async def count_by_severity_last_24h(self) -> dict[str, int]:
        """{severity → count} over the last 24 h. Missing buckets default to 0."""
        end = datetime.now(UTC)
        start = end - timedelta(hours=24)
        cursor = await self._conn.execute(
            "SELECT severity, COUNT(*) FROM alerts WHERE created_at >= ? GROUP BY severity",
            (_iso(start),),
        )
        rows = await cursor.fetchall()
        await cursor.close()
        result: dict[str, int] = {"low": 0, "medium": 0, "high": 0, "critical": 0}
        for row in rows:
            result[row[0]] = int(row[1])
        return result

    async def list_pending(self, *, limit: int = 50) -> list[Alert]:
        cursor = await self._conn.execute(
            "SELECT id, service, level, window_start, window_end, event_count,"
            " baseline_mean, baseline_stddev, z_score, severity, reasoning,"
            " created_at, dispatched_at, webhook_status"
            " FROM alerts WHERE webhook_status = 'pending'"
            " ORDER BY created_at ASC LIMIT ?",
            (limit,),
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return [self._row_to_alert(r) for r in rows]

    @staticmethod
    def _row_to_alert(row: Any) -> Alert:
        anomaly = AnomalyWindow(
            service=row[1],
            level=row[2],
            window_start=datetime.fromisoformat(row[3]),
            window_end=datetime.fromisoformat(row[4]),
            count=int(row[5]),
            baseline_mean=float(row[6]),
            baseline_stddev=float(row[7]),
            z_score=float(row[8]),
        )
        return Alert(
            id=UUID(row[0]),
            anomaly=anomaly,
            severity=row[9],
            reasoning=row[10],
            created_at=datetime.fromisoformat(row[11]),
            dispatched_at=datetime.fromisoformat(row[12]) if row[12] is not None else None,
            webhook_status=row[13],
        )


class OutboxRepository:
    """Persistence for the webhook_outbox transactional outbox.

    See `persistence/schema.sql` top-of-file comment for the
    outbox-pattern rationale (Richardson 2019).
    """

    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def enqueue(
        self,
        alert_id: UUID,
        payload: dict[str, Any],
        *,
        commit: bool = True,
    ) -> int:
        """Enqueue a webhook job.

        Set `commit=False` to compose with the alert insert inside
        the same transaction (true outbox pattern; see
        `AlertService.create_and_enqueue`).
        """
        now = _iso(datetime.now(UTC))
        cursor = await self._conn.execute(
            "INSERT INTO webhook_outbox"
            "(alert_id, payload_json, enqueued_at, next_attempt_at)"
            " VALUES (?, ?, ?, ?)",
            (
                str(alert_id),
                json.dumps(payload, separators=(",", ":"), sort_keys=True, default=str),
                now,
                now,
            ),
        )
        if commit:
            await self._conn.commit()
        return cursor.lastrowid or 0

    async def claim_pending(
        self,
        *,
        limit: int = 10,
    ) -> list[tuple[int, UUID, dict[str, Any]]]:
        now = _iso(datetime.now(UTC))
        cursor = await self._conn.execute(
            "SELECT id, alert_id, payload_json FROM webhook_outbox"
            " WHERE status IN ('pending','failed')"
            " AND (next_attempt_at IS NULL OR next_attempt_at <= ?)"
            " ORDER BY enqueued_at ASC LIMIT ?",
            (now, limit),
        )
        rows = await cursor.fetchall()
        await cursor.close()
        return [(int(r[0]), UUID(r[1]), json.loads(r[2])) for r in rows]

    async def mark_delivered(self, outbox_id: int) -> None:
        await self._conn.execute(
            "UPDATE webhook_outbox SET status = 'delivered', last_attempt_at = ? WHERE id = ?",
            (_iso(datetime.now(UTC)), outbox_id),
        )
        await self._conn.commit()

    async def mark_failed(
        self,
        outbox_id: int,
        *,
        next_attempt_at: datetime,
    ) -> None:
        await self._conn.execute(
            "UPDATE webhook_outbox"
            " SET status = 'failed',"
            " last_attempt_at = ?,"
            " next_attempt_at = ?,"
            " attempts = attempts + 1"
            " WHERE id = ?",
            (
                _iso(datetime.now(UTC)),
                _iso(next_attempt_at),
                outbox_id,
            ),
        )
        await self._conn.commit()

    async def mark_dead_letter(self, outbox_id: int) -> None:
        await self._conn.execute(
            "UPDATE webhook_outbox SET status = 'dead_letter' WHERE id = ?",
            (outbox_id,),
        )
        await self._conn.commit()
