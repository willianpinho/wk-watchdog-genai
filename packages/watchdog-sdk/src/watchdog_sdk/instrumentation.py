"""Drop-in `logging` integration — the killer SDK feature.

A real customer adds three lines to their app:

    from watchdog_sdk import WatchdogClient
    from watchdog_sdk.instrumentation import instrument_logging
    instrument_logging(logging.getLogger(), client=WatchdogClient(...), service="my-app")

…and every log record their app emits is buffered, batched, and POSTed
to `wk-watchdog` as a `LogEventInput`. No code changes at the call sites.

Design choices
--------------
* **Buffering with size + interval triggers.** Flush when the buffer
  reaches `batch_size` records OR `flush_interval_s` seconds have
  elapsed (driven by a background thread). The batch endpoint accepts
  ≤ 1000 events per request; default `batch_size` is 100 to keep
  per-request latency bounded.
* **Stdlib level mapping.** `logging.WARNING` → `"WARN"` (matching the
  watchdog `LogLevel` Literal); everything else maps 1:1.
* **Never raise.** The base `logging.Handler.handleError` swallows
  exceptions — a logging handler that crashes the app is worse than
  one that drops a log line. The batch dispatcher catches transport
  errors and logs them via the stdlib root logger.
* **Thread safety.** All buffer mutations go through a single lock;
  the background flush thread takes the same lock.

Why not `logging.handlers.HTTPHandler`?
---------------------------------------
The stdlib `HTTPHandler` POSTs **one record per request**, which would
hammer the watchdog API with 1k req/s on a chatty app. It also does
NOT retry, does NOT batch, and serialises with `urlencode` (not JSON).
Our handler is a real client: batching + retry + JSON + the same
typed contract the rest of the SDK uses.
"""

from __future__ import annotations

import atexit
import contextlib
import logging
import threading
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

from watchdog_sdk.models import LogEventInput, LogLevel

if TYPE_CHECKING:
    from watchdog_sdk.client import WatchdogClient

_DEFAULT_BATCH_SIZE: Final[int] = 100
_DEFAULT_FLUSH_INTERVAL_S: Final[float] = 5.0

# stdlib `logging.WARNING` is the canonical name; the watchdog API
# uses the shorter `WARN`. Everything else is 1:1.
_LEVEL_NAME_MAP: Final[dict[str, LogLevel]] = {
    "DEBUG": "DEBUG",
    "INFO": "INFO",
    "WARNING": "WARN",
    "WARN": "WARN",
    "ERROR": "ERROR",
    "CRITICAL": "CRITICAL",
    "FATAL": "CRITICAL",
}


class WatchdogHandler(logging.Handler):
    """Buffers `LogRecord`s and flushes them as `LogEventInput` batches."""

    def __init__(
        self,
        client: WatchdogClient,
        *,
        service: str,
        batch_size: int = _DEFAULT_BATCH_SIZE,
        flush_interval_s: float = _DEFAULT_FLUSH_INTERVAL_S,
    ) -> None:
        super().__init__()
        self._client = client
        self._service = service
        self._batch_size = batch_size
        self._flush_interval = flush_interval_s

        self._buffer: list[LogEventInput] = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._flush_thread = threading.Thread(
            target=self._flush_loop,
            name="watchdog-sdk-flush",
            daemon=True,
        )
        self._flush_thread.start()
        atexit.register(self._on_atexit)

    # ------------------------------------------------------------------
    # logging.Handler API
    # ------------------------------------------------------------------

    def emit(self, record: logging.LogRecord) -> None:
        try:
            event = self._record_to_event(record)
        except Exception:  # noqa: BLE001 — logging.Handler must not raise
            self.handleError(record)
            return

        with self._lock:
            self._buffer.append(event)
            should_flush = len(self._buffer) >= self._batch_size
        if should_flush:
            self.flush()

    def flush(self) -> None:
        with self._lock:
            if not self._buffer:
                return
            batch = self._buffer
            self._buffer = []
        try:
            self._client.send_batch(batch)
        except Exception:  # noqa: BLE001 — never propagate from a handler
            logging.getLogger(__name__).warning(
                "watchdog-sdk batch flush failed; dropped %d records",
                len(batch),
                exc_info=True,
            )

    def close(self) -> None:
        self._stop.set()
        self.flush()
        super().close()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _record_to_event(self, record: logging.LogRecord) -> LogEventInput:
        level: LogLevel = _LEVEL_NAME_MAP.get(record.levelname.upper(), "INFO")
        return LogEventInput(
            ts=datetime.fromtimestamp(record.created, tz=UTC),
            service=self._service,
            level=level,
            message=record.getMessage(),
            attributes={
                "logger": record.name,
                "module": record.module,
                "function": record.funcName or "<module>",
                "line": str(record.lineno),
            },
        )

    def _flush_loop(self) -> None:
        while not self._stop.wait(self._flush_interval):
            self.flush()

    def _on_atexit(self) -> None:
        # Best-effort flush at interpreter shutdown so the last batch
        # isn't lost on process exit. Suppress everything — atexit hooks
        # cannot raise (they break interpreter teardown).
        with contextlib.suppress(Exception):
            self.flush()


def instrument_logging(  # noqa: PLR0913 — DI seam: all 6 kwargs are legitimate injection points
    logger: logging.Logger,
    *,
    client: WatchdogClient,
    service: str,
    level: int = logging.NOTSET,
    batch_size: int = _DEFAULT_BATCH_SIZE,
    flush_interval_s: float = _DEFAULT_FLUSH_INTERVAL_S,
) -> WatchdogHandler:
    """Attach a `WatchdogHandler` to `logger` and return it.

    The returned handler can be used to `.flush()` synchronously or
    `.close()` to stop the background flush thread. Most callers can
    ignore the return value — `atexit` will flush the last batch.
    """
    handler = WatchdogHandler(
        client,
        service=service,
        batch_size=batch_size,
        flush_interval_s=flush_interval_s,
    )
    handler.setLevel(level)
    logger.addHandler(handler)
    return handler


__all__ = ["WatchdogHandler", "instrument_logging"]
