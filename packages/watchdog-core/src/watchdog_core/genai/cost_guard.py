"""Token-cost guard + Prometheus counter for the GenAI path.

Holds a sliding 60-second window of token-consumption events; when the
sum exceeds the configured per-minute cap, `over_cap()` returns True,
the classifier forces a rule-based fallback, and a structured warning
is logged. The Prometheus counter `genai_tokens_total{model, kind}` is
incremented on every `record()` regardless of cap state — observability
applies even (especially) when we degrade.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable
from typing import cast

from prometheus_client import REGISTRY, Counter

_COUNTER_NAME = "genai_tokens_total"
_WINDOW_SECONDS = 60.0
_DEFAULT_TOKENS_PER_MINUTE_CAP = 100_000


def _get_or_create_counter() -> Counter:
    """Module-level Counter; safe under test re-imports.

    `prometheus_client.Counter(...)` raises if the metric is already
    registered, which happens when pytest collects modules that import
    this file from multiple test files in the same session. We look up
    the existing collector and reuse it.
    """
    try:
        return Counter(
            _COUNTER_NAME,
            "Total GenAI tokens consumed by the watchdog classifier.",
            ["model", "kind"],
        )
    except ValueError:
        # prometheus_client has no public lookup-by-name; private attr
        # is the only stable path for re-import safety.
        existing = list(REGISTRY._names_to_collectors.values())  # noqa: SLF001
        for collector in existing:
            if getattr(collector, "_name", None) == _COUNTER_NAME:
                return cast("Counter", collector)
        raise


_TOKENS_COUNTER = _get_or_create_counter()


class CostGuard:
    """Sliding-window per-minute token cap.

    Not thread-safe; intended for use inside a single asyncio event loop.
    """

    def __init__(
        self,
        *,
        tokens_per_minute_cap: int = _DEFAULT_TOKENS_PER_MINUTE_CAP,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._cap = tokens_per_minute_cap
        self._clock = clock or time.monotonic
        self._window: deque[tuple[float, int]] = deque()

    def record(self, *, input_tokens: int, output_tokens: int, model: str) -> None:
        """Add a usage event and bump the prometheus counter."""
        now = self._clock()
        self._window.append((now, input_tokens + output_tokens))
        self._evict_old(now)
        if input_tokens:
            _TOKENS_COUNTER.labels(model=model, kind="input").inc(input_tokens)
        if output_tokens:
            _TOKENS_COUNTER.labels(model=model, kind="output").inc(output_tokens)

    def over_cap(self) -> bool:
        now = self._clock()
        self._evict_old(now)
        total = sum(tokens for _, tokens in self._window)
        return total >= self._cap

    def current_window_tokens(self) -> int:
        now = self._clock()
        self._evict_old(now)
        return sum(tokens for _, tokens in self._window)

    def _evict_old(self, now: float) -> None:
        threshold = now - _WINDOW_SECONDS
        while self._window and self._window[0][0] < threshold:
            self._window.popleft()
