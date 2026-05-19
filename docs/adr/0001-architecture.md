# ADR-001: Foundational Architecture for wk-watchdog

| Field      | Value                                                                                             |
| ---------- | ------------------------------------------------------------------------------------------------- |
| Status     | **Accepted**                                                                                      |
| Date       | 2026-05-19                                                                                        |
| Deciders   | Willian Pinho (Lead Architect)                                                                    |
| Supersedes | —                                                                                                 |
| Related    | ADR-002 (TBD: dashboard rendering), ADR-003 (TBD: intelligence layer), ADR-004 (TBD: SDK surface) |

---

## Context

We are building **wk-watchdog**, an API-first Intelligent Observability & Event Watchdog. The system ingests events (logs, metrics, custom signals), persists them durably, detects anomalies through composable analyzers, surfaces incidents on a dashboard, and exposes a Python SDK for client integration. Hard constraints set by the brief:

- 4–6 hour MVP build window (16h hard ceiling)
- Free database mandated
- Python-first, API-first
- Solo developer, no team to absorb operational complexity
- Senior-grade architecture must be visible in the layout, not just claimed in the README

Four foundational decisions are taken in this ADR to avoid "we'll restructure later" tech debt that disproportionately punishes solo builds.

---

## Decision 1 — Runtime & Libraries

| Concern        | Choice                                       | Pinned version (lower bound) |
| -------------- | -------------------------------------------- | ---------------------------- |
| Language       | **CPython 3.12**                             | `3.12`                       |
| HTTP framework | **FastAPI**                                  | `>=0.115`                    |
| ASGI server    | **uvicorn[standard]**                        | `>=0.32`                     |
| DB driver      | **aiosqlite**                                | `>=0.20`                     |
| Validation     | **Pydantic v2**                              | `>=2.9`                      |
| Config         | **pydantic-settings**                        | `>=2.6`                      |
| Logging        | **structlog**                                | `>=24.4`                     |
| Tracing        | **opentelemetry-sdk** + FastAPI instrumentor | `>=1.27` / `>=0.48b0`        |
| HTTP client    | **httpx**                                    | `>=0.27`                     |

**Rationale.** Python 3.12 over 3.13: stable typing ergonomics, no free-threaded GIL maturity tax. FastAPI over Flask/Starlette-direct: free OpenAPI schema and Pydantic v2 integration directly answer the "API-first" mandate. aiosqlite over `sqlite3` blocking driver: the API is async end-to-end; mixing a blocking DB driver in an async route handler is a junior trap. structlog + OpenTelemetry: the watchdog must be observable about itself — junior engineers ship a watchdog without telemetry; seniors don't.

---

## Decision 2 — Three-Layer Architecture (with a documented refusal of full hexagonal)

Every application module under `apps/api/src/watchdog_api/` is required to obey:

```
routes/         ← HTTP parsing + response shaping       (FastAPI routers only, NO business logic, NO SQL)
  ↓
services/       ← Business rules, orchestration         (calls repositories; NO ORM/SQL imports)
  ↓
repositories/   ← aiosqlite queries                     (returns domain dataclasses; NO business rules)
```

Dependencies point one way: `routes → services → repositories`. The domain types live in the sibling package `watchdog_core` (zero infrastructure dependencies); both `services` and `repositories` may import from it; `watchdog_core` may not import from anything in `apps/`.

**Authority.** This is a deliberate subset of Robert C. Martin's _Clean Architecture_ (2017). Martin's Dependency Rule — "_source code dependencies must point only inward, toward higher-level policies_" (Ch. 22) — is enforced at the directory level so a code reviewer or a `ruff` import-rule can mechanically catch violations without reading prose. The three layers are the application's Interface Adapter, Use Case, and Gateway concentric rings collapsed for a single-application MVP.

**Trade-off vs full Hexagonal / Ports & Adapters.** A textbook Cockburn-style hexagonal layout would introduce explicit Port abstractions (`EventRepositoryPort`) with separate Adapter implementations (`SQLiteEventRepository`, `InMemoryEventRepository`). With one storage backend and one application, that costs three files per concern to enable a substitution we are not making. We therefore **defer hexagonal** until a second persistence backend becomes a credible need (see Decision 3's migration trigger). The three-layer rule captures the 80% testability benefit at 20% of the ceremony; promotion to ports is a mechanical refactor when motivated. Choosing to draw the line _here_ is itself the senior judgment call this ADR records.

---

## Decision 3 — SQLite over Postgres for the MVP

**Choice.** SQLite via `aiosqlite`, journal mode `WAL`, file at `./data/watchdog.sqlite`.

**Why.**

- **Zero ops.** No container, no auth surface, no backup pipeline, no port to expose. The MVP runs from a uv-managed venv with no out-of-process dependencies.
- **Single-writer is sufficient for MVP load.** SQLite in WAL mode sustains hundreds of small writes/sec on commodity SSD; the demo's ingest profile is comfortably inside that envelope.
- **Concurrent reads do not block writes** in WAL mode, which matches the dominant contention pattern (write-heavy ingest + read-heavy dashboard queries).
- **Pragmatic-engineering tax.** Standing up Postgres for a 4-hour MVP would be ceremony that a senior on a deadline would refuse to pay.

**Migration trigger — Postgres at sustained > 50 req/s or multi-writer requirements.** When ingest sustains > 50 req/s, _or_ when horizontal write scaling becomes credible, _or_ when we add multi-tenant row-level security, we migrate. The path is bounded because Decision 2 already isolated SQL inside the repository layer:

1. Promote `EventRepository` from concrete class to abstract base in `watchdog_core` (this is when hexagonal earns its keep).
2. Implement `PostgresEventRepository` using `asyncpg`.
3. Wire selection through a DI factory; service layer is unchanged.
4. Dual-write during cutover; backfill cold data from a SQLite snapshot.

Estimated migration effort _given_ this discipline: ~1 sprint. Estimated effort _without_ it: rewrite. That gap is the strategic value this ADR is buying.

---

## Decision 4 — Folder Layout: src-layout uv Virtual Workspace

```
wk-watchdog/
├── pyproject.toml                            ← uv VIRTUAL workspace root (no [project] section)
├── Makefile
├── docs/adr/
├── apps/
│   └── api/                                  ← The FastAPI application
│       ├── pyproject.toml                    (watchdog-api)
│       └── src/watchdog_api/
└── packages/
    ├── watchdog-core/                        ← Pure domain types
    │   ├── pyproject.toml                    (watchdog-core)
    │   └── src/watchdog_core/
    └── watchdog-sdk/                         ← Publishable Python client (placeholder until Turn 12)
        ├── pyproject.toml                    (watchdog-sdk)
        └── src/watchdog_sdk/
```

**Why src-layout** (`src/<pkg>/` rather than `<pkg>/` at the package root):

- Tests cannot accidentally import the in-tree source; they must import the _installed_ copy, which surfaces packaging bugs early.
- The classic "forgot to add the file to the package" mistake becomes a failed test, not a silent ImportError in production.
- This is the layout the Python Packaging Authority recommends for libraries.

**Why a uv virtual workspace** (root `pyproject.toml` carries only `[tool.uv.workspace]`; no top-level `[project]`):

- Single lockfile, single `.venv`, three independently-versionable packages — the operational model of a small monorepo without the build-tooling surface of a large one.
- The **SDK is a publishable artifact from day 1** — separate name, separate version, separate dependency surface. This directly answers the "designing SDKs" element of the Senior GenAI Engineer JD.
- `watchdog-core` gives the SDK its typing contract without forcing the SDK to depend on the application.
- The empty SDK package shipped this turn is intentional: it locks the import path and the dependency boundary _before_ any SDK code exists, so future SDK work doesn't accidentally couple to the application.

---

## Consequences

| Positive                                                                                 | Negative / accepted cost                                                                       |
| ---------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------- |
| Senior-grade layout from t = 0; no future restructure tax.                               | Three `pyproject.toml` files (root + 3 members) — slightly higher cognitive load on newcomers. |
| Each layer is independently testable; services can be unit-tested with fake repos.       | src-layout requires editable installs (`uv sync` handles this).                                |
| The SQLite→Postgres migration path is bounded _and_ documented before it's needed.       | The first hour was spent on layout, not on shipping a route — judged worthwhile.               |
| The SDK has an enforced boundary from day 1; "designing SDKs" is structural, not slogan. | Empty SDK package now feels overbuilt; payoff is at Turn 12.                                   |

---

## References

- Martin, R. C. _Clean Architecture: A Craftsman's Guide to Software Structure and Design_. Prentice Hall, 2017. (Ch. 22 — The Dependency Rule.)
- Cockburn, A. _Hexagonal Architecture_ (2005). https://alistair.cockburn.us/hexagonal-architecture/
- PyPA, _Packaging Python Projects — src layout vs. flat layout_. https://packaging.python.org/en/latest/discussions/src-layout-vs-flat-layout/
- Astral, _uv workspaces_. https://docs.astral.sh/uv/concepts/workspaces/
- SQLite, _Write-Ahead Logging_. https://sqlite.org/wal.html
