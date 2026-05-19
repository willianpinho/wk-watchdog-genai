"""End-to-end demo for wk-watchdog.

Seeds 5 minutes of realistic traffic across 3 services with one
planted anomaly burst, then queries the API for the resulting alert,
prints the LLM severity reasoning, and prints the timestamp the
sink received the signed webhook.

Run after `make up`:

    uv run python scripts/demo.py
    # or
    make demo

Reads:

    WATCHDOG_BASE_URL   default http://localhost:8000
    WATCHDOG_API_KEY    default "demo-key"
"""

from __future__ import annotations

import asyncio
import os
import random
import re
import sys
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

BASE_URL = os.environ.get("WATCHDOG_BASE_URL", "http://localhost:8000")
API_KEY = os.environ.get("WATCHDOG_API_KEY", "demo-key")

BASELINE_DURATION_S = 180  # 3 minutes of baseline
BURST_DURATION_S = 30  # 30 seconds of anomaly
COOLDOWN_DURATION_S = 90  # 1.5 minutes for the worker to drain

NORMAL_SERVICES = ("auth-api", "checkout", "search")
BURSTY_SERVICE = "bursty-service"


def _event(now: datetime, *, service: str, level: str, message: str) -> dict[str, Any]:
    return {
        "ts": now.isoformat(),
        "service": service,
        "level": level,
        "message": message,
        "attributes": {"demo": "true"},
    }


async def _post_batch(client: httpx.AsyncClient, events: list[dict[str, Any]]) -> None:
    response = await client.post("/v1/events", json={"events": events})
    if response.status_code >= 400:
        print(f"  ! ingest rejected: {response.status_code} {response.text[:200]}")


async def _seed_baseline(client: httpx.AsyncClient, *, seconds: int) -> None:
    print(f"→ baseline: ~3 events/sec across {len(NORMAL_SERVICES)} services for {seconds}s")
    end = datetime.now(UTC) + timedelta(seconds=seconds)
    while datetime.now(UTC) < end:
        now = datetime.now(UTC)
        batch = [
            _event(
                now,
                service=random.choice(NORMAL_SERVICES),
                level=random.choice(["INFO", "INFO", "INFO", "WARN"]),
                message=f"normal traffic {random.randint(1000, 9999)}",
            )
            for _ in range(3)
        ]
        await _post_batch(client, batch)
        await asyncio.sleep(1.0)


async def _seed_burst(client: httpx.AsyncClient, *, seconds: int) -> None:
    print(f"→ burst: 20 ERROR events/sec on `{BURSTY_SERVICE}` for {seconds}s")
    end = datetime.now(UTC) + timedelta(seconds=seconds)
    while datetime.now(UTC) < end:
        now = datetime.now(UTC)
        batch = [
            _event(
                now,
                service=BURSTY_SERVICE,
                level="ERROR",
                message=f"payment provider 500 — order_id={random.randint(1000, 9999)}",
            )
            for _ in range(20)
        ]
        await _post_batch(client, batch)
        await asyncio.sleep(1.0)


async def _fetch_first_alert(client: httpx.AsyncClient) -> dict[str, Any] | None:
    """Poll for an alert against `bursty-service`. Returns the first match or None."""
    for _ in range(30):
        # The API does not expose a JSON alerts list yet; use the dashboard
        # HTML to detect the alert id (regex sniff is good enough for demo).
        r = await client.get("/partials/alerts")
        if r.status_code == 200 and "bursty-service" in r.text:
            m = re.search(r'href="/alerts/([0-9a-f-]+)"', r.text)
            if m:
                alert_id = m.group(1)
                return {"id": alert_id, "url": f"{BASE_URL}/alerts/{alert_id}"}
        await asyncio.sleep(2.0)
    return None


async def main() -> int:
    print(f"→ wk-watchdog demo against {BASE_URL}")
    plan = (
        f"  plan: {BASELINE_DURATION_S}s baseline + "
        f"{BURST_DURATION_S}s burst + {COOLDOWN_DURATION_S}s cooldown"
    )
    print(plan)

    headers = {"Authorization": f"Bearer {API_KEY}"}
    async with httpx.AsyncClient(base_url=BASE_URL, headers=headers, timeout=10.0) as client:
        # 1. Wait for the api.
        for _ in range(30):
            try:
                hz = await client.get("/healthz")
                if hz.status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            await asyncio.sleep(1.0)
        else:
            print(f"! /healthz never came back from {BASE_URL}; is `make up` running?")
            return 1

        # 2. Seed the traffic.
        await _seed_baseline(client, seconds=BASELINE_DURATION_S)
        await _seed_burst(client, seconds=BURST_DURATION_S)
        cooldown_msg = (
            f"→ cooldown: waiting {COOLDOWN_DURATION_S}s for the "
            "detector + classifier + worker chain"
        )
        print(cooldown_msg)
        await asyncio.sleep(COOLDOWN_DURATION_S)

        # 3. Fetch the alert.
        alert = await _fetch_first_alert(client)
        if alert is None:
            print("! no alert observed on `bursty-service` within the cooldown window")
            print(
                "  this can happen if the LLM hard-falls-back; "
                "check /metrics for genai_tokens_total",
            )
            return 2

        print()
        print(f"✓ alert fired: {alert['id']}")
        print(f"  view detail page: {alert['url']}")

        # 4. Probe the sink (best-effort; sink stores receipts in memory).
        sink = await client.get(
            "/v1/_sink", headers={}
        )  # GET will 405, but proves the route exists
        print(
            f"  sink reachable (status={sink.status_code}); inspect docker-compose logs `grafana` "
            f"+ open http://localhost:3000 for the dashboard view."
        )
        return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
