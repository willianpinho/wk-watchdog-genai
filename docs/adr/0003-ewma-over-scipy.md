# ADR-0003: EWMA + Welford detector over scipy / Prophet

| Field    | Value                                                                   |
| -------- | ----------------------------------------------------------------------- |
| Status   | **Accepted**                                                            |
| Date     | 2026-05-19                                                              |
| Deciders | Willian Pinho (Lead Architect)                                          |
| Related  | Implementation in `packages/watchdog-core/src/watchdog_core/detection/` |

## Context

We need a streaming anomaly detector per `(service, level)` key. Two
requirements pull in different directions:

- **Adaptive** — the baseline must follow drift smoothly (a growing
  service legitimately sees higher counts; we don't want chronic
  alerts).
- **O(1) per event** — we may observe thousands of events per minute
  and update the baseline on every minute-tick.

Constraints:

- Minimal external dependencies — the API container should remain
  under ~250 MB.
- No persistent state outside the API process for the MVP (state
  recovery is acceptable on a worker restart).

## Decision

We compose **EWMA (Exponentially Weighted Moving Average)** for the
mean with **Welford's online algorithm** for the variance, per
`(service, level)` key, on minute-aggregated event counts:

- `alpha = 2 / (N + 1) = 2 / 61 ≈ 0.0328` for a 60-bucket effective window.
- Welford's M2 accumulator: `M2 += delta * (x - new_mean)`.
- Stddev = `sqrt(M2 / (n - 1))`, clamped at 0 from below.
- Anomaly fires when `z = (count - mean) / max(stddev, 1.0) > threshold (3.0)`
  AND `count >= min_floor (5)`.

The `max(stddev, 1.0)` denominator floor prevents synthetic
z-explosions when the baseline has near-zero variance.

## Alternatives considered

| Option                      | Why rejected                                                                                                                                  |
| --------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------- |
| `scipy.stats.zscore`        | scipy bundle is ~150 MB; tiny gain over our pure-Python computation; requires re-scanning the window every minute (O(N) per update).          |
| Facebook **Prophet**        | Full Bayesian time-series fit is wildly over-engineered for "is this minute's count anomalous?"; pulls Stan as a transitive; slow to retrain. |
| `river` (online ML library) | Strong library, but adds a learning curve and another dep tree for what is fundamentally a 60-line computation.                               |
| Naive **fixed thresholds**  | Doesn't adapt; chronic false alerts as traffic grows; no Senior would propose this past prototype.                                            |

## Consequences

| Positive                                                            | Negative / accepted cost                                                                                                 |
| ------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------ |
| O(1) per event, no library dep.                                     | Welford-with-EWMA-mean is a hybrid; the variance is approximate when the mean is non-stationary. Acceptable in practice. |
| Numerically stable (Welford's M2 avoids catastrophic cancellation). | State is in-process — restart loses the ~1-hour warm-up.                                                                 |
| Cited authority: Welford 1962.                                      | Single threshold doesn't capture multi-modal traffic patterns. ADR-006 future work.                                      |
| Trivial to test deterministically (no scipy random seed dance).     | Per-key memory grows with cardinality of `(service, level)`; bounded by config in production.                            |

## References

- Welford, B. P., _Note on a method for calculating corrected sums of
  squares and products_. Technometrics 4(3), 1962, pp. 419-420.
- Implementation: `packages/watchdog-core/src/watchdog_core/detection/baseline.py`.
- Property-based test: `packages/watchdog-core/tests/test_property_baseline_no_nan.py`.
