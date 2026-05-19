# wk-watchdog

> Intelligent Observability & Event Watchdog — built for the Wolters Kluwer Vibe Coding Challenge (Senior GenAI Engineer track).

**wk-watchdog** is an API-first Python 3.12 system that ingests events, persists them in a zero-ops SQLite store, detects anomalies through composable rule, statistical, and GenAI-assisted analyzers, and surfaces live incidents on a server-rendered dashboard. The codebase is structured as a `uv` virtual workspace with three independently-versionable artifacts — a FastAPI application (`watchdog-api`), a pure domain core (`watchdog-core`), and a publishable Python client (`watchdog-sdk`) — so the public SDK surface is decoupled from the application from day 1.

## Architecture at a glance

- **HTTP layer:** FastAPI + Pydantic v2 (auto-generated OpenAPI is the API contract).
- **Storage:** SQLite via `aiosqlite`, WAL mode, isolated behind a repository layer (see [ADR-001](docs/adr/0001-architecture.md)).
- **Intelligence:** rule engine + rolling-window statistics + Anthropic-backed incident summarization.
- **Self-observability:** structlog JSON + OpenTelemetry SDK — the watchdog watches itself.
- **SDK:** typed Python client with sync and async surfaces; share types with the core via `watchdog-core`.

## Getting started

```bash
make install          # uv sync — creates .venv, installs all workspace members + dev deps
make all              # lint + type + test (the green-bar gate)
make run              # uvicorn watchdog_api.main:app --reload
```

See [`docs/adr/`](docs/adr) for architectural decision records and [`prompts.md`](prompts.md) for the full prompt-engineering audit trail (Vibe Coding Challenge requirement).
