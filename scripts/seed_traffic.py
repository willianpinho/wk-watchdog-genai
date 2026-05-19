"""Synthetic traffic generator for the wk-watchdog demo stack.

Usage (against `make up`):

    uv run python scripts/seed_traffic.py
    uv run python scripts/seed_traffic.py --rate 50 --duration 300

`--rate` is the per-second target event rate (default 20). `--duration`
is how long to run for; 0 means forever. The generator simulates four
services with a realistic level distribution (INFO heavy, ERROR rare)
and OCCASIONALLY bursts ERROR/CRITICAL for `bursty-service` to make
the detector fire for a believable demo.
"""

from __future__ import annotations

import argparse
import asyncio
import random
import time
from datetime import UTC, datetime, timedelta

import httpx

_SERVICES = ("auth-api", "billing", "checkout", "search", "bursty-service")
_LEVELS = ("INFO", "INFO", "INFO", "INFO", "INFO", "WARN", "WARN", "ERROR", "CRITICAL")
_MESSAGES = (
    "user logged in",
    "cache miss",
    "slow query 230ms",
    "retry attempt 1",
    "circuit breaker half-open",
    "request 200 in 12ms",
    "kafka consumer lag 12",
    "smtp 421 retrying",
)


def _build_event(now: datetime, *, force_burst: bool = False) -> dict[str, object]:
    if force_burst:
        service = "bursty-service"
        level = random.choice(["ERROR", "CRITICAL"])
    else:
        service = random.choice(_SERVICES)
        level = random.choice(_LEVELS)
    return {
        "ts": now.isoformat(),
        "service": service,
        "level": level,
        "message": random.choice(_MESSAGES),
        "attributes": {"host": f"node-{random.randint(1, 20):02d}"},
    }


async def _send_batch(client: httpx.AsyncClient, events: list[dict[str, object]]) -> None:
    try:
        r = await client.post("/v1/events", json={"events": events}, timeout=5.0)
        if r.status_code >= 400:
            print(f"  ! batch rejected: {r.status_code} {r.text[:200]}")
    except httpx.HTTPError as exc:
        print(f"  ! HTTP error: {exc}")


async def run(*, base_url: str, rate: int, duration: int) -> None:
    start = time.monotonic()
    batch_size = max(1, rate)
    burst_period_s = 60  # every minute, blast bursty-service for ~3 seconds.

    async with httpx.AsyncClient(base_url=base_url) as client:
        cycle = 0
        while True:
            now = datetime.now(UTC)
            is_burst = (cycle % burst_period_s) < 3
            events = [
                _build_event(now - timedelta(milliseconds=i * 5), force_burst=is_burst)
                for i in range(batch_size)
            ]
            await _send_batch(client, events)
            cycle += 1
            if duration and time.monotonic() - start >= duration:
                return
            await asyncio.sleep(1.0)


def main() -> None:
    parser = argparse.ArgumentParser(description="wk-watchdog synthetic traffic generator")
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--rate", type=int, default=20, help="events/sec (per batch)")
    parser.add_argument(
        "--duration",
        type=int,
        default=0,
        help="seconds to run; 0 means until Ctrl-C",
    )
    args = parser.parse_args()
    print(f"→ seeding {args.rate} events/sec to {args.url} for {args.duration or '∞'}s")
    asyncio.run(run(base_url=args.url, rate=args.rate, duration=args.duration))


if __name__ == "__main__":
    main()
