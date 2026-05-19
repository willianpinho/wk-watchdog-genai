# watchdog-sdk

> Official Python client for the **wk-watchdog** Observability & Event API.
> Sync + async, typed end-to-end, with batching, jittered exponential retry,
> and a drop-in stdlib-`logging` integration.

```bash
pip install watchdog-sdk
# OpenTelemetry trace propagation is optional:
pip install 'watchdog-sdk[otel]'
```

## Quickstart

```python
from watchdog_sdk import LogEventInput, WatchdogClient
from datetime import UTC, datetime

with WatchdogClient(base_url="https://watchdog.your.host", api_key="…") as c:
    ack = c.send_event(LogEventInput(
        ts=datetime.now(UTC),
        service="payments",
        level="ERROR",
        message="stripe declined",
    ))
    assert ack.all_accepted
```

Or batched:

```python
events = [LogEventInput(ts=…, service="auth", level="WARN", message=m) for m in messages]
ack = c.send_batch(events)
print(f"accepted={ack.accepted} rejected={len(ack.rejected)}")
```

Async surface is identical:

```python
from watchdog_sdk import AsyncWatchdogClient

async with AsyncWatchdogClient(base_url="…", api_key="…") as c:
    await c.send_batch(events)
```

## Drop-in `logging` integration (the killer feature)

Three lines and every log record your app emits is buffered, batched, and
POSTed to the watchdog as a `LogEventInput`:

```python
import logging
from watchdog_sdk import WatchdogClient, instrument_logging

client = WatchdogClient(base_url="…", api_key="…")
instrument_logging(logging.getLogger(), client=client, service="my-app")

logging.error("order failed: id=%s", order_id)  # ← shows up in the watchdog
```

Defaults: batches of 100 records OR every 5 seconds, whichever fires first.
The handler runs a background flush thread + an `atexit` hook so the final
batch is never lost on graceful shutdown.

### Why not `logging.handlers.HTTPHandler`?

The stdlib `HTTPHandler` is the obvious-looking choice and the wrong one:

| Concern            | `HTTPHandler`            | `WatchdogHandler`                           |
| ------------------ | ------------------------ | ------------------------------------------- |
| Batching           | none (1 req / record)    | size + interval batches                     |
| Retry on 5xx / 429 | no                       | jittered exponential, `Retry-After`-aware   |
| Encoding           | `urlencode` form-data    | JSON, typed against the OpenAPI contract    |
| Error handling     | raises on transport fail | logs to stdlib root, never propagates       |
| Trace correlation  | no                       | `traceparent` injected if otel is installed |

If your service is chatty (≥ 100 records/min), `HTTPHandler` will hammer the
watchdog endpoint and lose data on the first 503. This SDK is a real client.

## Retry semantics

- **Retryable status set** (defaults): `{429, 500, 502, 503, 504}`.
- **Backoff** is `base * factor^attempt`, jittered ± `jitter`, clamped to
  `backoff_max`. With defaults (`base=0.5s, factor=2.0, jitter=0.1,
max=30s`) the schedule is roughly `0.5s → 1s → 2s → 4s → …`.
- **`Retry-After` is honoured.** The server can override the schedule
  (seconds or HTTP-date); the SDK still clamps to `backoff_max` so a
  hostile server can't force a multi-hour sleep.
- Tests can pin the schedule by stubbing `random.uniform`.

To customise:

```python
from watchdog_sdk import RetryPolicy, WatchdogClient

policy = RetryPolicy(max_retries=5, backoff_base=1.0, backoff_max=60.0)
client = WatchdogClient(base_url="…", api_key="…", retry_policy=policy)
```

## Observability — trace propagation

If the **calling** process has the OpenTelemetry SDK installed AND is inside
an active span, the SDK's outbound HTTP layer (httpx) will inject the W3C
`traceparent` header automatically — assuming the caller has run
`HTTPXClientInstrumentor().instrument()`. The watchdog API uses
`FastAPIInstrumentor`, so a single `traceparent` round-trips the request/
response/dispatcher chain and shows up as one continuous trace in Jaeger.

The SDK does **not** pull the OpenTelemetry SDK as a hard dependency —
unprivileged consumers get the typed surface without the otel weight.
Install the optional extra (`pip install 'watchdog-sdk[otel]'`) only if
you want the propagation. The SDK's contract is identical either way.

## Backlog

See [TODO.md](./TODO.md). The headline pending item is the SSE
`stream_alerts()` server-side endpoint; the typed client surface is
already shipped here.
