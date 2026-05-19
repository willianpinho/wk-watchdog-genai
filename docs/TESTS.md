# Test strategy

This document records the deliberate decisions behind the test suite so
the next engineer doesn't have to re-derive them.

## Marker taxonomy

| Marker        | Definition                                                                                     | Where to find it                                                                                           |
| ------------- | ---------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------- |
| `unit`        | Fast in-process tests with NO external I/O surface. Default.                                   | Auto-applied by repo-root `conftest.py` for any test not in the integration set and not explicitly marked. |
| `integration` | Uses `httpx.AsyncClient(ASGITransport(app))` and/or a `tmp_path` SQLite file. Sub-100 ms each. | Auto-applied for files in `conftest.py`'s `_INTEGRATION_FILES` set.                                        |
| `contract`    | OpenAPI conformance (schemathesis). Manual / CI only.                                          | `apps/api/tests/test_contract_openapi.py` (smoke) + `make test-contract` (full schemathesis run).          |
| `slow`        | Tests with intentionally large Hypothesis state spaces. Opt-in.                                | Hand-marked.                                                                                               |

`pytest` (no args) runs `unit or integration` per `addopts` in
`pyproject.toml`. `slow` and `contract` are explicit opt-ins.

## Why we don't use `testcontainers-python` for SQLite

SQLite has no server. The container would just be running `sqlite3 file.db`
inside a Docker image — adding a container-orchestration tax for zero
isolation gain. **Decision: `tmp_path` + `aiosqlite.connect()` against a
real file is sufficient.**

We still cover the on-disk path explicitly:

- `test_wal_mode_active` (in `test_repositories.py`) opens a real
  `tmp_path/test.sqlite`, applies migrations, and asserts
  `PRAGMA journal_mode` returns `wal`. In-memory databases cannot
  enter WAL mode, so this test would silently regress if we ever
  swapped the fixture to `:memory:`.
- `test_foreign_keys_enabled` does the same for `PRAGMA foreign_keys`.

When we promote to Postgres (per ADR-001 Decision 3), `testcontainers-python`
becomes the right tool — Postgres genuinely benefits from per-test
container isolation, and `testcontainers-python` has first-class support.
The repository layer's three-layer separation (services depend on
`LogEventRepositoryProtocol`, not aiosqlite) means the migration is a
mechanical swap of the test fixture, not a rewrite.

## Property-based tests

Two Hypothesis tests with a deliberate falsification target each:

1. `test_property_json_roundtrip.py` — `LogEvent.attributes`
   survive insert + select through SQLite's TEXT-as-JSON column. If a
   future change introduces a custom encoder or a column-truncation
   bug, the property fails on the first generated example.
2. `test_property_baseline_no_nan.py` — `EWMABaseline.current()`
   never returns NaN or infinity for any int sequence. Catches the
   class of numerical bugs where Welford's M2 update or the EWMA
   smoothing factor accidentally goes wrong.

The Hypothesis settings cap `max_examples` to keep wall-clock under
the gate; `slow` opts-in for higher example counts in CI.

## Test data builders (DAMP > DRY)

`packages/watchdog-core/tests/builders.py` exposes `make_log_event`,
`make_anomaly`, `make_alert`. Tests read at the call site EVERY field
that's load-bearing for the assertion; readers don't have to chase
fixtures.

Reference: Khorikov 2020, _Unit Testing Principles, Practices, and
Patterns_, Ch. 4.

## Mutation testing (manual, signal-only)

```bash
uv run mutmut run --paths-to-mutate packages/watchdog-core/src/watchdog_core/detection
```

Configured in `pyproject.toml` `[tool.mutmut]`. NOT run in CI (too slow,
flaky on async paths). The point is to surface tests that "cover" code
without actually constraining its behaviour — the detection package is
where subtle off-by-one bugs would do the most damage, so we mutate
there first.

## Per-package coverage gates

`make test-core` runs only `packages/watchdog-core/tests/` with
`--cov-fail-under=90`.
`make test-api` runs only `apps/api/tests/` with `--cov-fail-under=80`.

The default `make test` runs the union under the global 80 % gate. CI
runs all three to gate against the higher per-package thresholds.
