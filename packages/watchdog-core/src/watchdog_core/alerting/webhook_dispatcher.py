"""Webhook dispatcher with Stripe-style HMAC-SHA256 signatures.

Contract
--------
`WebhookDispatcher.dispatch(alert, target_url, secret) -> DispatchResult`

  * Serializes the alert to a stable JSON body.
  * Signs the body with HMAC-SHA256 against `secret` and a fresh
    timestamp, encoded into the `X-Watchdog-Signature: t=<ts>,v1=<hex>`
    header (Stripe convention; cleartext separation of timestamp and
    digest so receivers can both check freshness and recompute).
  * Sets `X-Watchdog-Idempotency-Key: <alert id>` so receivers can
    de-duplicate redeliveries.
  * Times out at 5 seconds.
  * NEVER raises on HTTP failure — caller-friendly. All error paths
    fold into `DispatchResult(success=False, ...)`.

Replay protection
-----------------
The timestamp is embedded in the signed string (`f"{ts}.{body}"`) so a
replay of an old payload with the same signature fails the receiver's
freshness check (`abs(now - ts) <= tolerance`). The receiver enforces
this; the dispatcher just emits a fresh `t=` per attempt.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

import httpx

from watchdog_core.observability.metrics import WEBHOOK_DELIVERY_LATENCY

if TYPE_CHECKING:
    from watchdog_core.domain.models import Alert

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT_S = 5.0
_DEFAULT_TOLERANCE_S = 300


@dataclass(frozen=True)
class DispatchResult:
    """The outcome of one dispatch attempt."""

    success: bool
    status_code: int | None
    error: str | None
    latency_ms: int
    attempt: int


def serialize_alert(alert: Alert) -> bytes:
    """Stable JSON body for signing and sending.

    `sort_keys=True` guarantees byte-equivalent representations across
    Python versions, so the receiver can recompute the same signature.
    """
    return json.dumps(
        {
            "alert_id": str(alert.id),
            "service": alert.anomaly.service,
            "level": alert.anomaly.level,
            "severity": alert.severity,
            "reasoning": alert.reasoning,
            "z_score": alert.anomaly.z_score,
            "count": alert.anomaly.count,
            "baseline_mean": alert.anomaly.baseline_mean,
            "baseline_stddev": alert.anomaly.baseline_stddev,
            "window_start": alert.anomaly.window_start.isoformat(),
            "window_end": alert.anomaly.window_end.isoformat(),
            "created_at": alert.created_at.isoformat(),
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def sign_payload(payload: bytes, secret: str, ts: int) -> str:
    """Compute `t=<ts>,v1=<hex>` HMAC-SHA256 signature header value."""
    signing_string = f"{ts}.".encode() + payload
    digest = hmac.new(secret.encode("utf-8"), signing_string, hashlib.sha256).hexdigest()
    return f"t={ts},v1={digest}"


def verify_signature(
    payload: bytes,
    signature_header: str,
    secret: str,
    *,
    tolerance_s: int = _DEFAULT_TOLERANCE_S,
    now: int | None = None,
) -> bool:
    """Verify a Stripe-style signature header against `payload` and `secret`.

    Checks freshness (timestamp within `tolerance_s` of `now`) AND
    HMAC equality via `hmac.compare_digest` (constant-time).
    """
    try:
        parts = dict(p.split("=", 1) for p in signature_header.split(","))
        ts = int(parts["t"])
        provided = parts["v1"]
    except (KeyError, ValueError):
        return False

    current = now if now is not None else int(time.time())
    if abs(current - ts) > tolerance_s:
        return False

    expected_header = sign_payload(payload, secret, ts)
    _, _, expected = expected_header.partition("v1=")
    return hmac.compare_digest(provided, expected)


class WebhookDispatcher:
    """HTTP webhook delivery with HMAC signing. Never raises on failure."""

    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        timeout: float = _DEFAULT_TIMEOUT_S,
    ) -> None:
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self._timeout = timeout
        self._owns_client = client is None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def dispatch(
        self,
        alert: Alert,
        target_url: str,
        secret: str,
        *,
        attempt: int = 1,
    ) -> DispatchResult:
        payload = serialize_alert(alert)
        ts = int(time.time())
        signature = sign_payload(payload, secret, ts)
        headers = {
            "Content-Type": "application/json",
            "X-Watchdog-Signature": signature,
            "X-Watchdog-Idempotency-Key": str(alert.id),
        }
        start = time.monotonic()
        try:
            resp = await self._client.post(
                target_url,
                content=payload,
                headers=headers,
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            latency_ms = int((time.monotonic() - start) * 1000)
            logger.warning(
                "webhook dispatch HTTP error",
                extra={"alert_id": str(alert.id), "error": str(exc), "attempt": attempt},
            )
            WEBHOOK_DELIVERY_LATENCY.labels(outcome="error").observe(latency_ms / 1000.0)
            return DispatchResult(
                success=False,
                status_code=None,
                error=type(exc).__name__,
                latency_ms=latency_ms,
                attempt=attempt,
            )

        latency_ms = int((time.monotonic() - start) * 1000)
        success = resp.is_success
        WEBHOOK_DELIVERY_LATENCY.labels(
            outcome="success" if success else "failure",
        ).observe(latency_ms / 1000.0)
        return DispatchResult(
            success=success,
            status_code=resp.status_code,
            error=None if success else f"HTTP {resp.status_code}",
            latency_ms=latency_ms,
            attempt=attempt,
        )
