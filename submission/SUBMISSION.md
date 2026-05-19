# Reviewer reading order

Estimated time to a complete read: 30 minutes. The repo is structured
so a Senior reviewer can stop at any of the three checkpoints below
and still have a defensible picture.

## 5 minutes — get it running, read the headline docs

1. `README.md` — mission, 30-second quickstart, architecture diagram,
   tech-choices table with alternatives + why-rejected columns,
   production-readiness matrix.
2. `docs/adr/0001-architecture.md` — the foundational ADR (runtime
   stack, three-layer rule with Clean-Architecture citation and an
   explicit refusal of full hexagonal as senior-judgment, SQLite +
   migration trigger, src-layout uv workspace).
3. Run the demo:
   ```bash
   make up
   make demo
   ```
   Opens up to:
   - `http://localhost:8000` — server-rendered dashboard with the
     event-rate chart and recent-alerts list.
   - `http://localhost:3000` — Grafana, anonymous-Viewer, pre-provisioned
     `wk-watchdog overview` dashboard with 4 panels.
   - `http://localhost:16686` — Jaeger trace UI.

If `make up` is offline, the same paths can be exercised against the
in-process app via the CI test set (see checkpoint 3).

## 15 minutes — read the load-bearing code

In this order:

1. **`packages/watchdog-core/src/watchdog_core/services/alert_service.py`** —
   `AlertService.create_and_enqueue` is the actual transactional outbox
   write (one transaction, alert + webhook_outbox rows, rollback on
   error). This is the file the test `test_outbox_crash_safety.py`
   exercises with a monkey-patched failing outbox.
2. **`packages/watchdog-core/src/watchdog_core/genai/severity_classifier.py`** —
   the LLM call path. Note the **`tool_choice` enforcement** of
   `record_severity`, the **two-attempt retry on text-only responses**,
   the **deterministic rule-based fallback** with
   `model="rule-based-fallback"` so degradation is observable, and the
   **prompt-injection-defense** wired into the prompt template
   (`prompts/severity_v1.md`) plus the eval-tested adversarial case in
   `tests/test_severity_eval.py`.
3. **`packages/watchdog-core/src/watchdog_core/alerting/outbox_worker.py`** —
   single-worker outbox dispatcher. The module docstring documents the
   **single-instance commitment** (SQLite has no `SKIP LOCKED`) AND the
   N-worker Postgres-migration path. `tick()` is the public test seam;
   tests drive it directly without entering the run loop.

These three files together carry the architectural commitments. The
rest of the codebase is plumbing around them.

## 30 minutes — see the system breathe

1. **CI workflow** — `.github/workflows/ci.yml`. Six parallel jobs
   plus a `coverage-gate` aggregator. Per-package coverage thresholds
   (90 % `watchdog_core`, 80 % `watchdog_api`) enforced via
   `--override-ini="addopts=…"` to drop the default multi-package
   `--cov` source so each gate evaluates ONLY its target package.
2. **Watch a test run live:**
   ```bash
   gh run list --repo willianpinho/wk-watchdog-genai --workflow CI --limit 3
   gh run view <id> --repo willianpinho/wk-watchdog-genai
   ```
3. **Grafana dashboard** — open `http://localhost:3000` and click into
   the `wk-watchdog overview` dashboard. Four panels are pre-provisioned
   (event ingestion rate by level, anomalies by severity, webhook p95
   latency by outcome, GenAI tokens by kind). After `make demo`, all
   four should show data.
4. **SDK** — `packages/watchdog-sdk/README.md`. Note the
   **Why-not-`logging.handlers.HTTPHandler`** comparison table, the
   **PEP 484 explicit re-export pattern** in `__init__.py` (the formatter
   wars are documented in `prompts.md` Turn 13), and the **mypy-strict
   standalone test** (`tests/test_sdk_type_safety.py`) that asserts the
   SDK has no `watchdog_api` imports.
5. **`prompts.md`** — the meta-deliverable. 14 turns of human prompts +
   AI action summaries + verification output + honest discipline
   incidents (over-staging caught and recovered in Turn 2; three
   formatter races recovered in Turns 9/10/12; three rounds of
   schemathesis contract debugging in Turn 12). The final-turn
   retrospective is at the bottom.

## What is intentionally NOT in this repo

- **Production auth** — Bearer header is parsed but not validated.
  See README's Production-readiness matrix.
- **Multi-tenancy** — single-tenant data model.
- **Horizontal scale** — SQLite ceiling; ADR-0002 documents the
  Postgres migration trigger.
- **SSE `/v1/alerts/stream` server endpoint** — SDK ships the typed
  surface; server-side is on `packages/watchdog-sdk/TODO.md`.
- **The `submission/tagle-tag.{md,png}` files** — committed in this
  final submission turn (see the Turn 14 entry in `prompts.md`).

## Contact

- Repo: <https://github.com/willianpinho/wk-watchdog-genai>
- Email: `willianpinho@gmail.com`
