# ADR-0005: Transactional outbox for webhook delivery

| Field    | Value                                                                  |
| -------- | ---------------------------------------------------------------------- |
| Status   | **Accepted**                                                           |
| Date     | 2026-05-19                                                             |
| Deciders | Willian Pinho (Lead Architect)                                         |
| Related  | Implementation in `packages/watchdog-core/src/watchdog_core/alerting/` |

## Context

When the watchdog raises an alert, **two side effects must remain
atomic** with the database write to preserve correctness across
crashes:

1. Persist the alert row.
2. Send a webhook notification to the configured receiver.

A naive implementation performs (2) synchronously after committing
(1). A process crash, network blip, or container restart between
those steps **silently loses the notification** — the classic
DUAL-WRITE problem.

Constraints:

- SQLite is the MVP database (ADR-0002). No `SELECT … FOR UPDATE
SKIP LOCKED`, so the competing-consumer pattern is not available.
- Solo founder; we cannot tolerate "phantom alerts" in production.

## Decision

We implement the **transactional outbox** pattern (Richardson 2019,
_Microservices Patterns_, Ch. 3.2):

1. `AlertService.create_and_enqueue(alert, payload)` writes a row into
   `alerts` AND a row into `webhook_outbox` **inside the same
   transaction** (one `BEGIN` / `COMMIT`).
2. A separate **`OutboxWorker` coroutine** polls `webhook_outbox` for
   pending or failed-with-budget-remaining jobs, dispatches via the
   `WebhookDispatcher`, and updates the row's status (`delivered`,
   `failed` with `next_attempt_at`, or `dead_letter`).
3. The dispatcher signs every outbound payload with HMAC-SHA256 and
   the Stripe-style `X-Watchdog-Signature: t=<ts>,v1=<hex>` header so
   the receiver can verify authenticity AND freshness.
4. Retry policy: exponential backoff `1s → 4s → 16s → 64s`, max 5
   attempts, then `dead_letter`. Combined with HTTP-side idempotency
   keys at the receiver, this gives **at-least-once with no loss**.

A test (`test_outbox_crash_safety.py`) explicitly monkey-patches the
outbox-insert to raise mid-transaction and asserts that BOTH rows
roll back — the contract is mechanically verified.

## Why a single worker

Multi-worker competing-consumer would require atomic row claim with
`SELECT … FOR UPDATE SKIP LOCKED`. **SQLite has no such operator.**
Running two workers against a SQLite outbox would race on the same
row and either:

- dual-deliver (one worker marks delivered while the other is
  mid-flight, then the second succeeds too), OR
- deadlock under load.

We commit to a **single worker** for the SQLite MVP. The Postgres
migration trigger from ADR-0002 also unblocks N-worker scale-out
using `SKIP LOCKED`.

## Alternatives considered

| Option                             | Why rejected                                                                                                                          |
| ---------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------- |
| Naive POST-after-commit            | Loses messages on crash between commit and POST. Wrong on day 1.                                                                      |
| AWS SQS / Google Pub/Sub           | Adds cloud coupling for a free-DB MVP. Same correctness properties, more vendor surface.                                              |
| Event sourcing                     | Right answer at a different scale; introduces aggregate-replay complexity that adds zero MVP value.                                   |
| Synchronous webhook + caller retry | Pushes the retry burden onto every caller of the API. Wrong contract — the API's job is to NOT lose alerts, not to defer the problem. |

## Consequences

| Positive                                                                                        | Negative / accepted cost                                                                                                 |
| ----------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------ |
| At-least-once delivery; crashes are safe.                                                       | Single-worker bottleneck at high alert volume — see Postgres-migration trigger in ADR-0002.                              |
| Receiver-side idempotency via the `X-Watchdog-Idempotency-Key` header (alert id).               | Receivers must implement idempotent ingest — documented in the API contract.                                             |
| Observable: `webhook_delivery_latency_seconds{outcome}` histogram + `outbox_queue_depth` gauge. | Outbox table grows; periodic compaction is a future ops task.                                                            |
| Test-asserted atomicity.                                                                        | Two SQL inserts under one transaction — slightly higher per-alert latency than a single insert. Negligible at MVP scale. |

## References

- Richardson, C. _Microservices Patterns_. Manning, 2019. Ch. 3.2
  "Maintaining data consistency using the saga pattern" — section on
  the transactional outbox.
- Stripe API design conventions for webhook signatures.
- Implementation: `packages/watchdog-core/src/watchdog_core/alerting/`.
- Crash-safety test: `packages/watchdog-core/tests/test_outbox_crash_safety.py`.
