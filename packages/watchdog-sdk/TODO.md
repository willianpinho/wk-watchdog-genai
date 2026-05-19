# watchdog-sdk — backlog

Each entry below is a backlog ticket. The SDK ships the typed surface
for items 1 and 2 today so callers can write against the contract; the
implementation lands when the server-side pieces exist.

## 1. `GET /v1/alerts/stream` — server-side SSE endpoint

`AsyncWatchdogClient.stream_alerts()` currently raises
`NotImplementedError`. The server needs to expose a Server-Sent Events
endpoint at `GET /v1/alerts/stream` that emits one JSON-encoded `Alert`
per `data:` line, keeping the connection open with `:keep-alive`
comments.

When the route exists, the SDK side becomes:

```python
async def stream_alerts(self) -> AsyncIterator[Alert]:
    async with self._client.stream("GET", "/v1/alerts/stream") as resp:
        resp.raise_for_status()
        async for line in resp.aiter_lines():
            if line.startswith("data: "):
                yield Alert.model_validate_json(line[6:])
```

The `respx` test path for this is already sketched in the SDK design
notes; the integration test will look like the existing E2E pattern.

## 2. Optional `otel` extra — automatic instrumentation hook

`pip install 'watchdog-sdk[otel]'` already pulls
`opentelemetry-sdk>=1.27`, and the httpx call sites pick up
`traceparent` injection IF the caller has done
`HTTPXClientInstrumentor().instrument()`. We could ship a one-line
helper:

```python
from watchdog_sdk import enable_otel_propagation
enable_otel_propagation()
```

…that calls the instrumentor for them. Trivial; the only reason not to
ship today is keeping the API surface tight.

## 3. Lean SDK — split `watchdog-core` to drop heavy transitives

The SDK depends on `watchdog-core` for the Pydantic models. That brings
`anthropic`, `aiosqlite`, `prometheus-client`, `opentelemetry-api` as
transitive deps — heavy for an SDK that only needs the models.

Split `watchdog-core` into two:

- `watchdog-core-domain` — Pydantic models + hashing utilities only.
  Dependencies: `pydantic` (no aiosqlite, no anthropic, no otel).
- `watchdog-core-impl` — repositories + GenAI + detection + alerting.
  Depends on `watchdog-core-domain`.

The SDK then depends only on `watchdog-core-domain`. A real "thin SDK".
ADR-006 (TBD) will record the move when the dependency surface starts
to bite. Today it does not — but the lever is here when it does.

## 4. Stripe-style `idempotency_key` header support

The watchdog API doesn't require idempotency keys today, but a real
client SDK would let callers pass `idempotency_key="…"` so retries
under network partitions can't double-record events. Server side is
where the work is (a dedupe-by-key cache); this stub is a reminder
that the SDK should pass the header through opaquely as soon as the
server reads it.

## 5. Sync `stream_alerts` over chunked POST

`AsyncWatchdogClient.stream_alerts()` is the natural SSE shape.
`WatchdogClient` (sync) could expose the same iterator via
`httpx.Client.stream()`. Not yet implemented — see item 1 above for
the upstream blocker.
