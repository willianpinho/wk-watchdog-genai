"""SDK retry-policy tests — `respx` simulates the transient-503 path.

The retry policy is a pure function (see `retries.py`); these tests pin
`random.uniform` to 0 so the schedule is deterministic, then verify
that two 503s followed by a 200 result in two retries + final success.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch

import pytest
import respx
from httpx import Response

from watchdog_sdk import LogEventInput, RetryPolicy, WatchdogClient

_BASE_URL = "http://test"


def _event() -> LogEventInput:
    return LogEventInput(
        ts=datetime(2026, 5, 19, 12, 0, 0, tzinfo=UTC),
        service="retry-test",
        level="ERROR",
        message="boom",
    )


def test_503_503_200_succeeds_after_two_retries() -> None:
    with respx.mock(base_url=_BASE_URL, assert_all_called=False) as router:
        route = router.post("/v1/events").mock(
            side_effect=[
                Response(503, headers={"Retry-After": "0"}),
                Response(503, headers={"Retry-After": "0"}),
                Response(202, json={"accepted": 1, "rejected": []}),
            ],
        )
        with patch("watchdog_sdk.retries.random.uniform", return_value=0.0):
            client = WatchdogClient(
                base_url=_BASE_URL,
                api_key="x",
                max_retries=3,
            )
            ack = client.send_batch([_event()])
            client.close()

        assert route.call_count == 3
        assert ack.accepted == 1
        assert ack.rejected == []


def test_5xx_exhausts_budget_then_raises_for_status() -> None:
    """When the server keeps 5xx-ing past `max_retries`, the client
    surfaces the failure via `raise_for_status` rather than silently
    swallowing — callers must see the final 5xx."""
    with respx.mock(base_url=_BASE_URL, assert_all_called=False) as router:
        router.post("/v1/events").mock(
            return_value=Response(503, headers={"Retry-After": "0"}),
        )
        with patch("watchdog_sdk.retries.random.uniform", return_value=0.0):
            client = WatchdogClient(
                base_url=_BASE_URL,
                api_key="x",
                max_retries=2,
            )
            with pytest.raises(Exception, match="503"):
                client.send_batch([_event()])
            client.close()


def test_retry_policy_delay_jitter_clamped() -> None:
    policy = RetryPolicy(max_retries=3, backoff_base=1.0, backoff_factor=2.0, jitter=0.0)
    # No jitter → exact exponential schedule.
    assert policy.delay_for(0) == 1.0
    assert policy.delay_for(1) == 2.0
    assert policy.delay_for(2) == 4.0


def test_retry_after_seconds_honored() -> None:
    policy = RetryPolicy(backoff_max=30.0)
    assert policy.delay_for(0, retry_after_header="5") == 5.0
    # Server tries to wedge us for 1 hour; we clamp.
    assert policy.delay_for(0, retry_after_header="3600") == 30.0


def test_retry_after_invalid_falls_back_to_backoff() -> None:
    policy = RetryPolicy(backoff_base=1.0, jitter=0.0)
    assert policy.delay_for(0, retry_after_header="not-a-number") == 1.0


def test_should_retry_only_on_retryable_status() -> None:
    policy = RetryPolicy(max_retries=3)
    assert policy.should_retry(503, attempt=0)
    assert policy.should_retry(429, attempt=0)
    assert not policy.should_retry(400, attempt=0)
    assert not policy.should_retry(404, attempt=0)
    # Budget exhausted.
    assert not policy.should_retry(503, attempt=3)
