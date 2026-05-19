"""EWMABaseline numerical-stability + behavior tests."""

from __future__ import annotations

import math

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from watchdog_core.detection.baseline import EWMABaseline


def test_baseline_first_observation_becomes_mean() -> None:
    b = EWMABaseline()
    b.update(7)
    mean, stddev = b.current()
    assert mean == 7.0
    assert stddev == 0.0  # n < 2
    assert b.n == 1


def test_baseline_two_observations_yield_positive_stddev() -> None:
    b = EWMABaseline()
    b.update(10)
    b.update(20)
    mean, stddev = b.current()
    assert 10.0 < mean < 20.0
    assert stddev > 0.0


def test_baseline_constant_stream_converges() -> None:
    b = EWMABaseline()
    for _ in range(500):
        b.update(10)
    mean, stddev = b.current()
    assert math.isclose(mean, 10.0, abs_tol=1e-9)
    assert stddev < 1e-6  # essentially no variance on a constant input


@given(values=st.lists(st.integers(min_value=0, max_value=100_000), min_size=2, max_size=200))
@settings(
    deadline=None,
    max_examples=50,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def test_property_stddev_is_finite_and_non_negative(values: list[int]) -> None:
    b = EWMABaseline()
    for v in values:
        b.update(v)
    mean, stddev = b.current()
    assert math.isfinite(mean)
    assert math.isfinite(stddev)
    assert stddev >= 0.0


@given(values=st.lists(st.integers(min_value=0, max_value=10_000), min_size=10, max_size=200))
@settings(
    deadline=None,
    max_examples=50,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def test_property_mean_stays_inside_observed_range(values: list[int]) -> None:
    b = EWMABaseline()
    for v in values:
        b.update(v)
    mean, _ = b.current()
    # EWMA mean is a convex combination of past values; it must lie
    # in the [min, max] envelope of observed counts.
    assert min(values) <= mean <= max(values)
