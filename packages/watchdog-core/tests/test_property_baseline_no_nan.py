"""Property test: `EWMABaseline.current()` never produces NaN or infinity.

A regression net for numerical bugs — if a future change to the
Welford / EWMA composition introduces catastrophic cancellation or
a division-by-zero, Hypothesis falsifies it almost immediately. We
let it explore a wide integer range (including 0) AND a wide stream
length to surface drift-or-overflow paths."""

from __future__ import annotations

import math

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from watchdog_core.detection.baseline import EWMABaseline


@given(
    values=st.lists(
        st.integers(min_value=0, max_value=1_000_000_000),
        min_size=1,
        max_size=500,
    ),
)
@settings(
    deadline=None,
    max_examples=75,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def test_baseline_current_is_always_finite(values: list[int]) -> None:
    b = EWMABaseline()
    for v in values:
        b.update(v)
    mean, stddev = b.current()
    assert math.isfinite(mean), f"mean became non-finite ({mean}) for input {values[:5]}…"
    assert math.isfinite(stddev), f"stddev became non-finite ({stddev}) for input {values[:5]}…"
    assert stddev >= 0.0


@given(
    n=st.integers(min_value=1, max_value=10_000),
)
@settings(deadline=None, max_examples=25)
def test_baseline_constant_stream_converges_without_drift(n: int) -> None:
    """A long stream of the SAME value must converge: mean == that value,
    stddev → 0. Catches a class of EWMA bugs where the smoothing factor
    accidentally has the wrong sign or leaks one-sided drift.
    """
    b = EWMABaseline()
    for _ in range(n):
        b.update(42)
    mean, stddev = b.current()
    assert math.isclose(mean, 42.0, abs_tol=1e-9)
    assert stddev < 1e-6
