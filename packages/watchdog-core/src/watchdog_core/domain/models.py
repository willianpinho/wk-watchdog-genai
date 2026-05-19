"""Pydantic v2 domain models for wk-watchdog.

All datetimes are timezone-aware (UTC). Models are `frozen=True` and
`extra="forbid"` so schema drift fails loud rather than silently.
No persistence, no HTTP — pure domain types.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

LogLevel = Literal["DEBUG", "INFO", "WARN", "ERROR", "CRITICAL"]
AlertSeverity = Literal["low", "medium", "high", "critical"]
WebhookStatus = Literal["pending", "delivered", "failed", "dead_letter"]


def _require_utc(value: datetime, *, field: str) -> datetime:
    if value.tzinfo is None:
        msg = f"{field} must be timezone-aware"
        raise ValueError(msg)
    return value.astimezone(UTC)


class LogEvent(BaseModel):
    """A single log event ingested from a producing service."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID
    ts: datetime
    service: str = Field(min_length=1, max_length=128)
    level: LogLevel
    message: str = Field(max_length=8192)
    attributes: dict[str, str] = Field(default_factory=dict)

    @field_validator("ts")
    @classmethod
    def _ts_utc(cls, v: datetime) -> datetime:
        return _require_utc(v, field="ts")


class AnomalyWindow(BaseModel):
    """A statistical window in which an anomaly was detected."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    service: str = Field(min_length=1)
    level: str = Field(min_length=1)
    window_start: datetime
    window_end: datetime
    count: int = Field(ge=0)
    baseline_mean: float
    baseline_stddev: float = Field(ge=0)
    z_score: float

    @field_validator("window_start", "window_end")
    @classmethod
    def _window_utc(cls, v: datetime) -> datetime:
        return _require_utc(v, field="window")


class Alert(BaseModel):
    """A decision to notify, derived from an anomaly window."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID
    anomaly: AnomalyWindow
    severity: AlertSeverity
    reasoning: str = Field(min_length=1, max_length=4096)
    created_at: datetime
    dispatched_at: datetime | None = None
    webhook_status: WebhookStatus = "pending"

    @field_validator("created_at")
    @classmethod
    def _created_utc(cls, v: datetime) -> datetime:
        return _require_utc(v, field="created_at")

    @field_validator("dispatched_at")
    @classmethod
    def _dispatched_utc(cls, v: datetime | None) -> datetime | None:
        if v is None:
            return None
        return _require_utc(v, field="dispatched_at")
