"""Retry policy with jittered exponential backoff and `Retry-After` honor.

Mirrors the urllib3 / requests retry conventions so users coming from
those libraries can predict our behaviour. The policy is pure: it
computes a delay, nothing else. The actual sleep happens in the sync
client (`time.sleep`) or async client (`asyncio.sleep`).

Design notes
------------
* `Retry-After` may be **seconds** or an **HTTP-date** (RFC 7231).
  Both are honored; we clamp to `backoff_max` to avoid a hostile
  server forcing a multi-hour sleep.
* Jitter is **symmetric** — `random.uniform(-jitter, jitter) * delay`
  — so the expected total backoff time matches the un-jittered
  schedule. Tests can pin `random.uniform = lambda lo, hi: 0` to
  make the schedule deterministic.
* Retryable status set defaults to `{429, 500, 502, 503, 504}` —
  the standard "transient server problem" family. 4xx other than
  429 is the client's fault and we don't retry it.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Final

_DEFAULT_RETRYABLE_STATUS: Final[frozenset[int]] = frozenset({429, 500, 502, 503, 504})


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Pure retry-delay calculator. Callers do the sleeping."""

    max_retries: int = 3
    backoff_base: float = 0.5
    backoff_factor: float = 2.0
    backoff_max: float = 30.0
    jitter: float = 0.1
    retryable_status: frozenset[int] = field(default_factory=lambda: _DEFAULT_RETRYABLE_STATUS)

    def should_retry(self, status_code: int, attempt: int) -> bool:
        """True if this status is in the retryable set AND we have budget left.

        `attempt` is 0-indexed (the first retry is attempt=1).
        """
        return status_code in self.retryable_status and attempt < self.max_retries

    def delay_for(self, attempt: int, retry_after_header: str | None = None) -> float:
        """Return seconds to sleep before the next attempt.

        If the server sent `Retry-After`, honor it (clamped to
        `backoff_max`). Otherwise compute jittered exponential
        backoff: `base * factor^attempt`.
        """
        if retry_after_header:
            parsed = self._parse_retry_after(retry_after_header)
            if parsed is not None:
                return min(parsed, self.backoff_max)

        exp = min(self.backoff_base * (self.backoff_factor**attempt), self.backoff_max)
        jitter_amt = random.uniform(-self.jitter, self.jitter) * exp
        return max(0.0, exp + jitter_amt)

    @staticmethod
    def _parse_retry_after(value: str) -> float | None:
        """Parse a Retry-After header. Returns seconds-from-now or None."""
        value = value.strip()
        try:
            return float(value)
        except ValueError:
            pass
        try:
            target = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        if target.tzinfo is None:
            target = target.replace(tzinfo=UTC)
        delta = (target - datetime.now(UTC)).total_seconds()
        return max(0.0, delta)
