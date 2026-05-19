# wk-watchdog — developer Makefile
# All targets run inside the uv-managed virtualenv via `uv run`.
# `make all` is the local equivalent of CI: lint → type → test.

.DEFAULT_GOAL := help
.PHONY: help install lint type test test-core test-api test-slow test-contract \
        run fmt all clean tree \
        up down logs seed demo docker-test docker-build

# Package source roots (used by mypy + ad-hoc tooling).
PACKAGES := \
	apps/api/src/watchdog_api \
	packages/watchdog-core/src/watchdog_core \
	packages/watchdog-sdk/src/watchdog_sdk

help:  ## Show this help.
	@awk 'BEGIN {FS = ":.*##"; printf "Usage:\n  make \033[36m<target>\033[0m\n\nTargets:\n"} /^[a-zA-Z_-]+:.*?##/ { printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

install:  ## Sync uv workspace (creates .venv, installs all members + dev deps).
	uv sync --all-packages

fmt:  ## Auto-fix: black + ruff --fix.
	uv run black .
	uv run ruff check --fix .

lint:  ## Lint: ruff check + black --check (zero tolerance).
	uv run ruff check .
	uv run black --check .

type:  ## Mypy strict on every package source.
	uv run mypy $(PACKAGES)

test:  ## Run unit + integration (default selection per pyproject addopts).
	uv run pytest

test-core:  ## Full suite, but with the watchdog_core 90% coverage gate.
	# We run the FULL test suite (not just packages/watchdog-core/tests/) because
	# the integration tests in apps/api/tests/ also exercise watchdog_core code
	# (repositories, services, detection). The --override-ini drops the
	# default [tool.coverage.run].source set (all 3 packages) so the gate
	# is computed over watchdog_core ONLY.
	uv run pytest \
		--override-ini="addopts=--strict-markers --strict-config -m 'unit or integration'" \
		--cov=watchdog_core --cov-fail-under=90 --cov-report=term-missing

test-api:  ## Full suite, but with the watchdog_api 80% coverage gate.
	uv run pytest \
		--override-ini="addopts=--strict-markers --strict-config -m 'unit or integration'" \
		--cov=watchdog_api --cov-fail-under=80 --cov-report=term-missing

test-slow:  ## Opt-in: Hypothesis-heavy / large-state-space tests.
	uv run pytest -m slow

test-contract:  ## Schemathesis full OpenAPI conformance (CI step, slow).
	@echo "→ start the app on :8000 first (make run) then run:"
	@echo "  uv run schemathesis run http://localhost:8000/openapi.json --checks all --hypothesis-deadline=2000"

run:  ## Run the API in dev mode (auto-reload).
	uv run uvicorn watchdog_api.main:app --reload --host 0.0.0.0 --port 8000

all: install lint type test test-core test-api  ## Full local CI pipeline.

tree:  ## Print the tracked file tree (ignores .venv, _planning, caches).
	@command -v tree >/dev/null 2>&1 && tree -a -I '.git|.venv|__pycache__|.ruff_cache|.mypy_cache|.pytest_cache|_planning|node_modules|.DS_Store|*.egg-info|data|htmlcov|.coverage' || \
		find . \( -path ./.git -o -path ./.venv -o -path ./_planning -o -path '*/__pycache__' -o -path '*/.ruff_cache' -o -path '*/.mypy_cache' -o -path '*/.pytest_cache' -o -path '*.egg-info' -o -path ./data -o -path ./htmlcov \) -prune -o -print | sort

clean:  ## Remove caches and venv.
	rm -rf .venv .ruff_cache .mypy_cache .pytest_cache .coverage htmlcov data

# ----------------------------------------------------------------------------
# Docker / compose (Turn 10)
# ----------------------------------------------------------------------------

up:  ## docker compose up -d (api + otel + jaeger + prometheus + grafana).
	GIT_REV=$$(git rev-parse HEAD 2>/dev/null || echo unknown) \
	VERSION=$$(grep -E '^__version__' apps/api/src/watchdog_api/__init__.py | head -1 | awk -F'"' '{print $$2}') \
	docker compose up -d --build
	@echo ""
	@echo "  api      :  http://localhost:8000"
	@echo "  jaeger   :  http://localhost:16686"
	@echo "  prom     :  http://localhost:9090"
	@echo "  grafana  :  http://localhost:3000 (anon Viewer; admin/admin for editing)"

down:  ## docker compose down (keeps volumes).
	docker compose down

logs:  ## Tail logs from the stack.
	docker compose logs -f --tail=200

seed:  ## Generate synthetic traffic against the running api.
	uv run python scripts/seed_traffic.py --rate 20

demo:  ## End-to-end demo: 5 min synthetic traffic + planted anomaly burst + alert receipt.
	uv run python scripts/demo.py

deck:  ## Render the Marp slide deck to PDF (requires marp-cli on PATH).
	@command -v marp >/dev/null 2>&1 || { \
		echo "→ marp-cli not found. Install: npm i -g @marp-team/marp-cli"; \
		echo "  or via npx: npx @marp-team/marp-cli submission/presentation.md --pdf"; \
		exit 1; \
	}
	marp submission/presentation.md --pdf --output submission/presentation.pdf
	@echo "✓ wrote submission/presentation.pdf"

docker-build:  ## Build the production image (no compose).
	docker build \
		--build-arg GIT_REV=$$(git rev-parse HEAD 2>/dev/null || echo unknown) \
		--build-arg VERSION=$$(grep -E '^__version__' apps/api/src/watchdog_api/__init__.py | head -1 | awk -F'"' '{print $$2}') \
		-t wk-watchdog:dev .

docker-test:  ## Build the image, run smoke (/healthz + /readyz), tear down.
	bash scripts/test_docker_smoke.sh
