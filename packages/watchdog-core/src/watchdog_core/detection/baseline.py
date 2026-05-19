"""Per-key EWMA + Welford baseline for anomaly detection.

Algorithm rationale
-------------------
We maintain a per-(service, level) baseline of event-counts-per-minute
over a rolling 1-hour window. Two requirements pull in different
directions:

  * The baseline must adapt smoothly to drift (a growing service
    legitimately sees higher counts; we don't want chronic alerts).
  * The update path must be O(1) — we observe potentially thousands
    of events per minute and recompute on every minute-tick.

Composing **exponentially-weighted moving average (EWMA)** for the
mean with **Welford's online algorithm** for the variance is the
standard solution. EWMA gives us a 60-bucket effective window via
`alpha = 2 / (N + 1)` (=2/61 ≈ 0.0328); Welford's M2 accumulator
keeps the variance numerically stable without re-summing history.

Citation: B. P. Welford, "Note on a method for calculating corrected
sums of squares and products", Technometrics 4(3), 1962, pp. 419-420.

Numerical-stability notes
-------------------------
Welford's M2 update uses `delta * (x - new_mean)` precisely to avoid
the catastrophic cancellation of the textbook `sum_xx - mean^2 * n`
form. We additionally clamp the resulting variance at 0 from below to
absorb tiny negative residues from floating-point roundoff.
"""

from __future__ import annotations

import math

# 60 one-minute buckets ⇒ alpha = 2/(N+1) per the common EWMA convention.
_DEFAULT_ALPHA = 2.0 / 61.0
# Welford's variance is undefined for fewer than 2 samples.
_MIN_SAMPLES_FOR_STDDEV = 2


class EWMABaseline:
    """Pure, deterministic baseline. No I/O, no time, no globals.

    Methods:
    -------
    update(count)  — fold one bucket count into the running stats.
    current()      — return (mean, stddev).
    """

    __slots__ = ("_alpha", "_m2", "_mean", "_n")

    def __init__(self, alpha: float = _DEFAULT_ALPHA) -> None:
        self._alpha = alpha
        self._n = 0
        self._mean = 0.0
        self._m2 = 0.0

    def update(self, count: int) -> None:
        """Incorporate one closed-bucket count."""
        self._n += 1
        if self._n == 1:
            # Bootstrap on first sample to avoid biasing toward 0.
            self._mean = float(count)
            return
        delta = count - self._mean
        # EWMA mean update.
        self._mean = self._alpha * count + (1.0 - self._alpha) * self._mean
        # Welford's M2 update against the *new* mean.
        delta2 = count - self._mean
        self._m2 += delta * delta2

    def current(self) -> tuple[float, float]:
        """Return (mean, stddev). Stddev is 0 until n >= 2."""
        if self._n < _MIN_SAMPLES_FOR_STDDEV:
            return self._mean, 0.0
        variance = max(self._m2 / (self._n - 1), 0.0)
        return self._mean, math.sqrt(variance)

    @property
    def n(self) -> int:
        return self._n
