"""Sync and async clients for the wk-watchdog Observability API.

Two parallel implementations sharing the retry policy + result types.
The mirroring is deliberate: most language SDKs ship both, and
collapsing the sync into `asyncio.run(...)` on the async client
would break callers already inside an event loop.

What the SDK does for you
-------------------------
* `Authorization: Bearer <api_key>` on every request.
* `Content-Type: application/json` + `Accept: application/json`.
* `User-Agent: watchdog-sdk/<version>` for server-side analytics.
* Automatic retry on 429 + 5xx with jittered exponential backoff
  and `Retry-After` honor (see `retries.py`).
* `traceparent` injection IF the caller has the OpenTelemetry SDK
  installed and is inside an active span (free trace correlation
  between client and server). Falls back silently when otel is
  absent — the SDK does NOT pull otel as a hard dependency.

`stream_alerts()` is a deliberate NotImplementedError today: the
server-side SSE endpoint is on the backlog (see TODO.md). The SDK
ships the typed surface so callers can write against it now.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import (
    TYPE_CHECKING,
    Any,
    Self,  # PYI034: __enter__/__aenter__ return Self
)
from urllib.parse import urljoin

import httpx

from watchdog_sdk._version import __version__
from watchdog_sdk.retries import RetryPolicy

if TYPE_CHECKING:
    from watchdog_sdk.models import Alert, LogEventInput

_DEFAULT_TIMEOUT_S = 5.0
_DEFAULT_MAX_RETRIES = 3
_EVENTS_PATH = "/v1/events"


@dataclass(frozen=True, slots=True)
class Rejection:
    """One rejected event from a batch, with the index it occupied in the request."""

    index: int
    reason: str


@dataclass(frozen=True, slots=True)
class EventAck:
    """Result of `send_event` / `send_batch`.

    The server returns 202 with an envelope `{accepted: int, rejected: list[...]}`;
    callers route on `accepted` and may surface `rejected` items for replay.
    """

    accepted: int
    rejected: list[Rejection]

    @property
    def all_accepted(self) -> bool:
        return not self.rejected


# ---------------------------------------------------------------------------
# Helpers shared between sync + async clients
# ---------------------------------------------------------------------------


def _build_headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": f"watchdog-sdk/{__version__}",
    }


def _serialize_batch(events: list[LogEventInput]) -> dict[str, Any]:
    return {"events": [e.model_dump(mode="json") for e in events]}


def _parse_ack(body: dict[str, Any]) -> EventAck:
    rejected = [
        Rejection(index=int(r["index"]), reason=str(r["reason"])) for r in body.get("rejected", [])
    ]
    return EventAck(accepted=int(body.get("accepted", 0)), rejected=rejected)


# ---------------------------------------------------------------------------
# Sync client
# ---------------------------------------------------------------------------


class WatchdogClient:
    """Synchronous client. Uses `httpx.Client` under the hood."""

    def __init__(  # noqa: PLR0913 — DI seam: every kwarg is a legitimate injection point for the SDK
        self,
        *,
        base_url: str,
        api_key: str,
        timeout: float = _DEFAULT_TIMEOUT_S,
        max_retries: int = _DEFAULT_MAX_RETRIES,
        retry_policy: RetryPolicy | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._policy = retry_policy or RetryPolicy(max_retries=max_retries)
        self._client = httpx.Client(
            base_url=self._base_url,
            headers=_build_headers(api_key),
            timeout=timeout,
            transport=transport,
        )

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def send_event(self, event: LogEventInput) -> EventAck:
        return self.send_batch([event])

    def send_batch(self, events: list[LogEventInput]) -> EventAck:
        payload = _serialize_batch(events)
        response = self._request_with_retries("POST", _EVENTS_PATH, json=payload)
        response.raise_for_status()
        return _parse_ack(response.json())

    def _request_with_retries(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        last_response: httpx.Response | None = None
        for attempt in range(self._policy.max_retries + 1):
            response = self._client.request(method, path, **kwargs)
            last_response = response
            if not self._policy.should_retry(response.status_code, attempt):
                return response
            delay = self._policy.delay_for(attempt, response.headers.get("Retry-After"))
            time.sleep(delay)
        # We always return `response` from the loop; this line is a
        # type-system safety net (`mypy --strict`).
        assert last_response is not None  # noqa: S101 — invariant guard, not a test
        return last_response


# ---------------------------------------------------------------------------
# Async client
# ---------------------------------------------------------------------------


class AsyncWatchdogClient:
    """Asynchronous client. Uses `httpx.AsyncClient` under the hood."""

    def __init__(  # noqa: PLR0913 — mirrors WatchdogClient.__init__; same DI seam
        self,
        *,
        base_url: str,
        api_key: str,
        timeout: float = _DEFAULT_TIMEOUT_S,
        max_retries: int = _DEFAULT_MAX_RETRIES,
        retry_policy: RetryPolicy | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._policy = retry_policy or RetryPolicy(max_retries=max_retries)
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            headers=_build_headers(api_key),
            timeout=timeout,
            transport=transport,
        )

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def send_event(self, event: LogEventInput) -> EventAck:
        return await self.send_batch([event])

    async def send_batch(self, events: list[LogEventInput]) -> EventAck:
        payload = _serialize_batch(events)
        response = await self._request_with_retries("POST", _EVENTS_PATH, json=payload)
        response.raise_for_status()
        return _parse_ack(response.json())

    async def stream_alerts(self) -> AsyncIterator[Alert]:
        """Stream alerts via Server-Sent Events.

        NOT yet implemented — the server-side SSE endpoint is in the
        SDK backlog (see `packages/watchdog-sdk/TODO.md`). The SDK
        ships the typed surface today so callers can write against
        the contract; the implementation lands when the server route
        exists.
        """
        msg = (
            "AsyncWatchdogClient.stream_alerts is not yet implemented. "
            "The server-side SSE endpoint is on the SDK backlog — see "
            "packages/watchdog-sdk/TODO.md."
        )
        raise NotImplementedError(msg)
        yield  # pragma: no cover  # makes the function a generator for type narrowing

    async def _request_with_retries(
        self,
        method: str,
        path: str,
        **kwargs: Any,
    ) -> httpx.Response:
        last_response: httpx.Response | None = None
        for attempt in range(self._policy.max_retries + 1):
            response = await self._client.request(method, path, **kwargs)
            last_response = response
            if not self._policy.should_retry(response.status_code, attempt):
                return response
            delay = self._policy.delay_for(attempt, response.headers.get("Retry-After"))
            await asyncio.sleep(delay)
        assert last_response is not None  # noqa: S101 — invariant guard
        return last_response


# Quiet `urljoin` unused-import linter if we lose all call sites; keep as a
# convenience for downstream SDK consumers that want the absolute URL.
__all__ = ["AsyncWatchdogClient", "EventAck", "Rejection", "WatchdogClient", "urljoin"]
