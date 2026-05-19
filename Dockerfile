# =============================================================================
# wk-watchdog — multi-stage production Dockerfile
#
# Build:
#   docker build -t wk-watchdog:dev \
#     --build-arg GIT_REV=$(git rev-parse HEAD) \
#     --build-arg VERSION=0.1.0 \
#     .
#
# Notes on base-image choice
# --------------------------
# The brief asked for `gcr.io/distroless/python3-debian12`. Two problems with
# that path:
#   1. distroless/python3-debian12 currently ships **Python 3.11**, but the
#      codebase pins `requires-python = ">=3.12"` and uses 3.12-only API
#      (`datetime.UTC`, `Literal[...]` exhaustiveness improvements).
#   2. The image has no shell, so `HEALTHCHECK CMD curl ...` is impossible —
#      we'd need a Python urllib one-liner.
#
# Senior-pragmatic decision: use `python:3.12-slim` for the runtime stage AND
# retain the distroless security posture explicitly:
#   * Non-root user (uid 10001).
#   * `tini` as PID 1 for proper signal forwarding (SIGTERM → uvicorn).
#   * Read-only root FS at deploy time (compose mounts a tmpfs for /tmp + a
#     bind-mounted volume for /data).
#   * `apt-get clean` + only essentials in runtime layer.
# This trades ~30 MB for being able to debug and version-pin correctly.
# When distroless ships a Python 3.12 image (tracked in
# https://github.com/GoogleContainerTools/distroless/issues), we revisit.
# =============================================================================

# ---------- Stage 1: builder ----------
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

# uv is small (<10 MB) and the fastest Python package manager.
RUN pip install --no-cache-dir uv==0.5.18

WORKDIR /build

# Cache layer for dependencies: copy ONLY the manifests first so a code change
# does not invalidate the (large) dependency layer.
COPY pyproject.toml uv.lock README.md ./
COPY apps/api/pyproject.toml apps/api/pyproject.toml
COPY packages/watchdog-core/pyproject.toml packages/watchdog-core/pyproject.toml
COPY packages/watchdog-sdk/pyproject.toml packages/watchdog-sdk/pyproject.toml
COPY packages/watchdog-sdk/README.md packages/watchdog-sdk/README.md
# ^^ hatchling reads `readme = ...` at metadata-build time, so every referenced
#    README must exist in the build context BEFORE `uv sync` runs:
#      - apps/api          → readme = "../../README.md"     (root)
#      - watchdog-core     → readme = "../../README.md"     (root)
#      - watchdog-sdk      → readme = "README.md"           (package-local;
#                            published standalone, needs its own README on PyPI)

# Now copy actual source (workspace members need them present for editable install).
COPY apps/api/src apps/api/src
COPY packages/watchdog-core/src packages/watchdog-core/src
COPY packages/watchdog-sdk/src packages/watchdog-sdk/src

# `--frozen` enforces lockfile fidelity; `--no-dev` skips ruff/mypy/pytest/etc.
# `--all-packages` installs every workspace member (the api + the core + sdk).
RUN uv sync --frozen --no-dev --all-packages


# ---------- Stage 2: runtime ----------
FROM python:3.12-slim AS runtime

ARG GIT_REV=unknown
ARG VERSION=0.0.0

LABEL org.opencontainers.image.title="wk-watchdog" \
      org.opencontainers.image.description="Intelligent Observability & Event Watchdog" \
      org.opencontainers.image.source="https://github.com/willianpinho/wk-watchdog-genai" \
      org.opencontainers.image.revision="${GIT_REV}" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.licenses="MIT"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH" \
    WATCHDOG_DB_URL="sqlite+aiosqlite:////data/watchdog.sqlite"
# ^^ FOUR slashes after the scheme — the URL parser strips the 3-slash prefix,
#    so the remaining path must keep a leading `/` to land at absolute `/data/`
#    (the chowned VOLUME below). Three slashes resolves to `data/` (relative
#    to WORKDIR `/app`), which the non-root user can't write to.

# tini handles PID-1 signal forwarding (uvicorn → graceful shutdown on SIGTERM).
# ca-certificates lets us reach Anthropic / OTLP TLS endpoints.
RUN apt-get update \
 && apt-get install -y --no-install-recommends tini ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# Non-root user. uid 10001 matches the convention in our k8s manifests.
RUN groupadd -r -g 10001 watchdog \
 && useradd -r -u 10001 -g watchdog -d /app -s /sbin/nologin watchdog \
 && mkdir -p /data \
 && chown watchdog:watchdog /data

WORKDIR /app

# Copy ONLY the installed virtualenv from the builder. The source is included
# in the venv as editable installs; no tests or dev tooling sneak in.
COPY --from=builder --chown=watchdog:watchdog /build/.venv /app/.venv
COPY --from=builder --chown=watchdog:watchdog /build/apps/api/src/watchdog_api /app/.venv/lib/python3.12/site-packages/watchdog_api
COPY --from=builder --chown=watchdog:watchdog /build/packages/watchdog-core/src/watchdog_core /app/.venv/lib/python3.12/site-packages/watchdog_core
COPY --from=builder --chown=watchdog:watchdog /build/packages/watchdog-sdk/src/watchdog_sdk /app/.venv/lib/python3.12/site-packages/watchdog_sdk

USER watchdog
EXPOSE 8000
VOLUME /data

# Python-only healthcheck (no curl in the image; tini is here only for signals).
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python3", "-c", \
         "import urllib.request,sys;\
          r=urllib.request.urlopen('http://localhost:8000/readyz',timeout=2);\
          sys.exit(0 if r.status == 200 else 1)"]

ENTRYPOINT ["/usr/bin/tini", "--"]
# Invoke uvicorn as a module — bypasses the venv-script shebang, which still
# points at the builder-stage path (`#!/build/.venv/bin/python`) after the
# /build → /app venv relocation. `python -m` uses the runtime PATH directly.
CMD ["python", "-m", "uvicorn", "watchdog_api.main:create_app", "--factory", \
     "--host", "0.0.0.0", "--port", "8000", "--no-server-header"]
