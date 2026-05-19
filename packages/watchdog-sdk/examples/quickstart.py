"""Runnable quickstart for watchdog-sdk.

Boot the demo stack first:

    make up               # docker compose up -d
    uv run python packages/watchdog-sdk/examples/quickstart.py

What it does (top to bottom):
    1. Instantiates a sync WatchdogClient against http://localhost:8000.
    2. Wires Python `logging` to flow into the SDK via instrument_logging.
    3. Emits a steady baseline of INFO records, then bursts ERRORs so the
       server-side anomaly detector reliably fires.
    4. Flushes + closes the handler. atexit would also catch it, but
       calling flush() explicitly is a good habit in scripts.
    5. Prints what the SDK acked back. The Grafana dashboard at
       http://localhost:3000 should show the spike within ~15 s.
"""

from __future__ import annotations

import logging
import sys
import time
from datetime import UTC, datetime

from watchdog_sdk import (
    LogEventInput,
    WatchdogClient,
    instrument_logging,
)

_BASE_URL = "http://localhost:8000"
_API_KEY = "demo-key"


def main() -> None:
    print(f"→ connecting to {_BASE_URL}", file=sys.stderr)
    with WatchdogClient(base_url=_BASE_URL, api_key=_API_KEY) as client:
        # ---------------- 1. direct send_event ----------------------------
        ack = client.send_event(
            LogEventInput(
                ts=datetime.now(UTC),
                service="quickstart",
                level="INFO",
                message="quickstart starting",
            ),
        )
        print(f"  send_event acked: accepted={ack.accepted} rejected={len(ack.rejected)}")

        # ---------------- 2. instrumented stdlib logging ------------------
        logger = logging.getLogger("quickstart")
        logger.setLevel(logging.DEBUG)
        handler = instrument_logging(
            logger,
            client=client,
            service="quickstart",
            batch_size=20,
            flush_interval_s=1.0,
        )

        # Baseline — 60 INFOs over ~6 s.
        for i in range(60):
            logger.info("baseline tick %d", i)
            time.sleep(0.1)

        # Spike — 50 ERRORs in one burst.
        for i in range(50):
            logger.error("payment failed: order=%d", 1000 + i)

        handler.flush()

        # ---------------- 3. batched send_batch ---------------------------
        batch = [
            LogEventInput(
                ts=datetime.now(UTC),
                service="quickstart",
                level="WARN",
                message=f"batch tail {i}",
            )
            for i in range(10)
        ]
        ack = client.send_batch(batch)
        print(f"  send_batch acked: accepted={ack.accepted} rejected={len(ack.rejected)}")

        handler.close()

    print(
        "✓ quickstart done. Open http://localhost:3000 (grafana) — the spike "
        "should be visible in the event-rate + anomaly panels.",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
