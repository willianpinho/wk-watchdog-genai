# Prompts Audit Log — WK Vibe Coding Challenge

> **Project:** Intelligent Observability & Event Watchdog
> **Role context:** Senior GenAI Engineer (Wolters Kluwer / Andela)
> **Discipline:** This log captures _every_ prompt the human pasted into the Claude Code session, verbatim. The human did not edit code manually at any point. The AI (Claude Code, model `claude-opus-4-7[1m]`) wrote every line of code, every config file, every doc.
> **Timer started:** 2026-05-19 (t=0)

---

## Turn 1 — Kickoff & Rules of Engagement

**Timestamp:** t = 0:00
**Elapsed at end of turn:** ~0:05

**Human prompt (verbatim):**

```
Lead Architect mode: ON. We are building a Python-based, API-first Intelligent Observability & Event Watchdog using a free database (SQLite) and a dashboard. Rules: - No Manual Edits: You provide all logic and fixes. I will not edit any code. - Audit Log: You must maintain a file named prompts.md. After every turn, update that file (or provide the text block) with the prompt I just used. - Time-Check: Start a timer. Goal is an MVP in 4-6 hours (Max window: 16h). Report 'Elapsed Time' at the end of every response. Acknowledge and let's start.
```

**AI action summary:**

1. Acknowledged the four operating constraints (no manual edits, audit log mandatory, time-boxed, elapsed-time reporting).
2. Read repo state (`.gitignore`, `submission/`, `_planning/EXECUTION-START.md`) to confirm scope and discipline rules. Did **not** read `_planning/prompts/prompts-execution.md` — pre-reading the upcoming turns would defeat the turn-by-turn pacing.
3. Created this `prompts.md` and logged Turn 1.
4. Proposed initial architecture (FastAPI + SQLite + async SQLAlchemy + Jinja/HTMX dashboard + GenAI-assisted intelligence layer + Python SDK) and asked the human to confirm or redirect before any code is written.

**Notes / discipline incidents:** None.

---

## Turn 2 — ADR-001 + Full Repo Scaffold (`make all` green)

**Timestamp:** t ≈ 0:05
**Elapsed at end of turn:** ~0:25

**Human prompt (verbatim):**

```
Before writing any code, output a concise architecture decision record (ADR-001) covering:
1. Chosen runtime: Python 3.12, FastAPI, uvicorn, aiosqlite, Pydantic v2, structlog, OpenTelemetry SDK, httpx.
2. Three-layer rule: routes (HTTP) -> services (business logic, no SQL) -> repositories (aiosqlite queries only). Justify with one paragraph citing Clean Architecture (Martin 2017) and the trade-off vs hexagonal.
3. Why SQLite over Postgres for this challenge (zero-ops, single-writer fine for MVP, WAL mode for concurrent reads). Note the migration path to Postgres in case throughput >50 req/s.
4. Folder layout for src-layout with `apps/api`, `packages/watchdog-core`, `packages/watchdog-sdk` (the SDK package is a Senior differentiator and will be filled in later).

After the ADR, generate the full repo skeleton with these files actually written to disk:
- pyproject.toml at the root using uv/hatch with workspace members for `apps/api`, `packages/watchdog-core`, `packages/watchdog-sdk`. Pin ruff, black, mypy (strict), pytest, pytest-asyncio, pytest-cov, httpx, respx, hypothesis. Configure mypy strict, ruff with rules ALL minus a justified ignore list, black line-length 100, pytest with asyncio_mode=auto and addopts="--cov --cov-fail-under=80 --strict-markers --strict-config".
- .python-version (3.12)
- .gitignore (Python + venv + .ruff_cache + .mypy_cache + .pytest_cache + coverage + .env)
- .env.example with only placeholders (no secrets), referencing 1Password URIs in comments
- README.md placeholder with one-paragraph mission
- docs/adr/0001-architecture.md (the ADR you just drafted)
- Makefile with targets: install, lint, type, test, run, fmt, all
- apps/api/src/watchdog_api/__init__.py with `__version__`
- packages/watchdog-core/src/watchdog_core/__init__.py
- packages/watchdog-sdk/src/watchdog_sdk/__init__.py

Quality bar: `make all` must pass with zero ruff warnings, mypy strict zero errors, and pytest reporting "no tests collected" cleanly. Print the file tree at the end. Then update prompts.md and report Elapsed Time.
```

**AI action summary:**

1. **ADR-001 drafted** at `docs/adr/0001-architecture.md` — four decisions (runtime, three-layer, SQLite-vs-Postgres with migration trigger >50 req/s, src-layout uv virtual workspace). Cited Martin 2017 (Clean Architecture, Ch. 22 — Dependency Rule) for the three-layer choice and explicitly recorded the _refusal_ of full Cockburn hexagonal as a deliberate senior trade-off (defer ports until a second persistence backend is credible).
2. **Repo skeleton materialised on disk** as 11 files:
   - `pyproject.toml` — virtual uv workspace (no `[project]`), `[tool.uv.workspace]` with glob members, PEP 735 `[dependency-groups]` for dev deps, ruff `ALL` minus a _commented-and-justified_ ignore list (D100–D107 / D203 / D213 / COM812 / ISC001 / ANN401 / FIX002 / TD002 / TD003 / CPY001), mypy `strict = true`, black `line-length = 100`, pytest `asyncio_mode = "auto"` + `addopts = "--cov --cov-fail-under=80 --strict-markers --strict-config"` exactly per the brief, plus `[tool.coverage.run]` to give `--cov` a source target.
   - `apps/api/pyproject.toml`, `packages/watchdog-core/pyproject.toml`, `packages/watchdog-sdk/pyproject.toml` — each a real `[project]` + `hatchling` build backend, declared runtime deps (the api pyproject pins all 9 runtime libs called for in the brief), so the workspace shape is real even though no code uses them yet.
   - `apps/api/src/watchdog_api/__init__.py` carries `__version__ = "0.1.0"`; the core + sdk `__init__.py` also carry `__version__` for consistent introspection.
   - `.python-version` (`3.12`), `.env.example` (placeholders + 1Password `op://` URI comments — no real secrets), `README.md` (one-paragraph mission + ADR link), `Makefile` (install / lint / type / test / run / fmt / all / clean / tree / help — `test` target tolerates pytest exit-5 cleanly during scaffold phase via `find … | grep -q .` guard).
3. **`.gitignore` preserved** — the existing file already covered the brief's requirements (Python, venv, all tooling caches, `.env`) AND carried the discipline-mandated `_planning/` exclusion from `EXECUTION-START.md`. Overwriting risked losing that carve-out, so we left it intact and noted the choice here.
4. **`make all` ran green on the first attempt:**
   - `uv sync --all-packages` → 58 packages resolved, 3 workspace members installed editable.
   - `ruff check .` → `All checks passed!`
   - `black --check .` → 3 files unchanged.
   - `mypy --strict` on the 3 package sources → `Success: no issues found in 3 source files`.
   - `pytest` → `no tests collected in 0.46s` (scaffold phase, handled gracefully by the Makefile guard).

**File tree at end of turn:**

```
wk-watchdog/
├── .env.example
├── .gitignore                                 (preserved from kickoff)
├── .python-version
├── Makefile
├── README.md
├── apps/
│   └── api/
│       ├── pyproject.toml
│       └── src/watchdog_api/__init__.py
├── docs/
│   └── adr/
│       └── 0001-architecture.md
├── packages/
│   ├── watchdog-core/
│   │   ├── pyproject.toml
│   │   └── src/watchdog_core/__init__.py
│   └── watchdog-sdk/
│       ├── pyproject.toml
│       └── src/watchdog_sdk/__init__.py
├── prompts.md
├── pyproject.toml                             (uv virtual workspace root)
├── submission/
│   ├── tagle-tag.md                           (UNTRACKED on disk — reserved for final submission commit)
│   └── tagle-tag.png                          (UNTRACKED on disk — reserved for final submission commit)
└── uv.lock                                    (generated by uv sync)
```

**Final Turn 2 commit message:** `feat(turn-2): ADR-001 + full repo scaffold (make all green)` — scaffold + ADR only, `submission/tagle-tag.{md,png}` intentionally **not** included.

**Notes / discipline incidents:**

1. **Over-staging incident (caught + corrected).** My first Turn 2 commit (since discarded) used `git add .` per the kickoff cadence rule and inadvertently swept the human's pre-existing untracked `submission/tagle-tag.{md,png}` into the snapshot. The human caught this, did a hard reset of the bad commits, and instructed me to re-commit Turn 2 without those assets. I re-staged by **explicit paths** (`git add prompts.md .env.example .python-version Makefile README.md apps/ docs/ packages/ pyproject.toml uv.lock`) and recommitted cleanly. Root cause: I followed the kickoff's `git add . && commit` convention without applying the "prefer adding specific files by name" rule that exists exactly to prevent this class of accident. **Lesson recorded:** for the remainder of the challenge, every commit stages files by explicit path; `git add .` is banned outside of the final submission turn.
2. No manual file edits were made by the human at any point. The correction was performed by the AI on instruction.
3. `make all` passed on the first attempt against the actually-tracked tree; no debug loop needed.

---
