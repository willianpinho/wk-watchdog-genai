# ADR-0002: SQLite over Postgres for the MVP

| Field                | Value                                                                  |
| -------------------- | ---------------------------------------------------------------------- |
| Status               | **Accepted**                                                           |
| Date                 | 2026-05-19                                                             |
| Deciders             | Willian Pinho (Lead Architect)                                         |
| Supersedes / Related | Refines ADR-0001 Decision 3; promotes the decision to a standalone ADR |

## Context

The Vibe-Coding brief mandates a "free database." We need durable
storage for `log_events`, `alerts`, and `webhook_outbox` — a write-heavy
ingest path plus a dashboard read path plus the outbox dispatcher's
poll loop. Throughput requirements at the demo are bounded
(< 50 req/s sustained); production ingest could be higher.

We are a solo team on a 4-to-6-hour MVP window. Standing up Postgres
implies a separate container, auth surface, backup pipeline, and
schema migration story on day one.

## Decision

We use **SQLite via `aiosqlite`** with **WAL journal mode** and
`PRAGMA foreign_keys = ON`. The DB file lives at `./data/watchdog.sqlite`
(mounted volume in the Docker deployment).

Rationale:

- **Zero ops** — no container, no port, no auth, no backup pipeline.
- **WAL mode** — concurrent reads do not block writes, matching the
  write-heavy ingest + read-heavy dashboard workload.
- **Single-writer is sufficient** — SQLite in WAL mode sustains
  hundreds of small writes/sec on commodity SSD. The demo's ingest
  profile is comfortably inside that envelope.
- **Pragmatic-engineering tax** — A senior on a deadline would refuse
  to pay the Postgres ceremony tax for an MVP at this scale.

## Migration trigger to Postgres

We commit to migrate when **any** of these is true:

- Sustained ingest > 50 req/s for > 10 minutes.
- Need for horizontal write scaling (multi-instance API).
- Multi-tenant requirements (row-level security).
- Need for `SELECT … FOR UPDATE SKIP LOCKED` (outbox competing consumers — see ADR-0005).

The bounded migration path:

1. Promote `LogEventRepository` / `AlertRepository` / `OutboxRepository`
   from concrete classes to abstract bases in `watchdog_core` (this is
   when hexagonal architecture earns its keep — see ADR-0001 Decision 2).
2. Implement `PostgresEventRepository` etc. using `asyncpg`.
3. DI factory switches based on `WATCHDOG_DB_URL` scheme.
4. Dual-write during cutover; backfill cold data from SQLite snapshot.

Estimated migration effort _given_ the three-layer discipline ADR-0001
already enforces: **~1 sprint**. Without it: rewrite. That gap is the
strategic value the discipline buys.

## Consequences

| Positive                                     | Negative / accepted cost                                                        |
| -------------------------------------------- | ------------------------------------------------------------------------------- |
| Zero ops overhead for the MVP.               | Hard ceiling at ~50 req/s sustained.                                            |
| Migration path is bounded and documented.    | No `SKIP LOCKED` ⇒ outbox worker is single-instance (ADR-0005).                 |
| WAL mode gives us concurrent reads.          | No row-level security ⇒ multi-tenant is out of scope.                           |
| Smaller attack surface (no exposed DB port). | DB file is a single point of failure; backup is `cp` (acceptable for the demo). |

## References

- SQLite, _Write-Ahead Logging_. https://sqlite.org/wal.html
- Hipp, D. R., _Appropriate Uses For SQLite_. https://sqlite.org/whentouse.html
- This ADR refines ADR-0001 Decision 3.
