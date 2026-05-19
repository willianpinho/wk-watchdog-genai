#!/usr/bin/env bash
# Docker smoke test: build, run, hit /healthz + /readyz, tear down.
#
# Gated behind `make docker-test` so CI can opt-out (the build takes
# ~90s and Docker isn't always available on every runner).

set -euo pipefail

IMAGE="wk-watchdog:smoke"
NAME="wk-watchdog-smoke-$$"
PORT=8001  # avoid colliding with a dev compose stack on :8000

cleanup() {
  set +e
  echo "→ cleanup: removing container $NAME"
  docker rm -f "$NAME" >/dev/null 2>&1
}
trap cleanup EXIT

echo "→ building $IMAGE"
docker build \
  --build-arg GIT_REV="$(git rev-parse HEAD 2>/dev/null || echo unknown)" \
  --build-arg VERSION="$(grep -E '^__version__' apps/api/src/watchdog_api/__init__.py | head -1 | awk -F'"' '{print $2}')" \
  -t "$IMAGE" \
  .

echo "→ image size:"
docker image inspect "$IMAGE" --format '{{.Size}}' | awk '{printf "  %.1f MB\n", $1/1024/1024}'

echo "→ running $NAME on :$PORT"
docker run -d --rm \
  --name "$NAME" \
  -p "$PORT:8000" \
  -e WATCHDOG_OTEL_ENABLED=false \
  "$IMAGE" >/dev/null

echo "→ waiting for /healthz to respond..."
for _ in $(seq 1 30); do
  if curl -fsS "http://localhost:$PORT/healthz" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

echo "→ /healthz:"
curl -fsS "http://localhost:$PORT/healthz" | head -1

echo "→ /readyz:"
curl -fsS "http://localhost:$PORT/readyz" | head -1

echo "✓ docker smoke passed"
