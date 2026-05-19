# wk-watchdog — developer Makefile
# All targets run inside the uv-managed virtualenv via `uv run`.
# `make all` is the local equivalent of CI: lint → type → test.

.DEFAULT_GOAL := help
.PHONY: help install lint type test run fmt all clean tree

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

test:  ## Pytest; tolerates "no tests collected" cleanly during scaffold phase.
	@if find apps packages -path '*/tests/*' -name 'test_*.py' 2>/dev/null | grep -q .; then \
		uv run pytest; \
	else \
		echo "→ scaffold phase: no tests collected yet (this is expected on Turn 2)"; \
		uv run pytest --co --no-cov -q || true; \
	fi

run:  ## Run the API in dev mode (auto-reload).
	uv run uvicorn watchdog_api.main:app --reload --host 0.0.0.0 --port 8000

all: install lint type test  ## Full local CI pipeline.

tree:  ## Print the tracked file tree (ignores .venv, _planning, caches).
	@command -v tree >/dev/null 2>&1 && tree -a -I '.git|.venv|__pycache__|.ruff_cache|.mypy_cache|.pytest_cache|_planning|node_modules|.DS_Store|*.egg-info|data|htmlcov|.coverage' || \
		find . \( -path ./.git -o -path ./.venv -o -path ./_planning -o -path '*/__pycache__' -o -path '*/.ruff_cache' -o -path '*/.mypy_cache' -o -path '*/.pytest_cache' -o -path '*.egg-info' -o -path ./data -o -path ./htmlcov \) -prune -o -print | sort

clean:  ## Remove caches and venv.
	rm -rf .venv .ruff_cache .mypy_cache .pytest_cache .coverage htmlcov data
