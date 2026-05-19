# ADR-0006: HTMX + server-rendered Jinja over an SPA framework

| Field    | Value                                                                                                      |
| -------- | ---------------------------------------------------------------------------------------------------------- |
| Status   | **Accepted**                                                                                               |
| Date     | 2026-05-19                                                                                                 |
| Deciders | Willian Pinho (Lead Architect)                                                                             |
| Related  | Implementation in `apps/api/src/watchdog_api/routes/dashboard.py` + `apps/api/src/watchdog_api/templates/` |

## Context

The watchdog needs an operator-facing console: 24-hour event count
overview, alerts-by-severity card, top-N noisy-services card, a
live event-rate chart, and a recent-alerts list with drill-down to
an alert detail page. The audience is the on-call ops team for the
watchdog itself — not external customers, not a SaaS brand surface.

Constraints:

- Time budget — the dashboard is one of 13+ turns in a 4–6 h MVP
  window.
- No Node toolchain in the repo (the rest is Python).
- Page weight ≤ 100 KB (Lighthouse-style sanity bar).
- Supply chain — no runtime CDN that could be hijacked.

## Decision

We render the dashboard **server-side with Jinja2 templates, swap
fragments via HTMX, and render the one time-series chart with
vendored uPlot**:

- `GET /` returns the full HTML document (layout + overview cards +
  chart mount + alerts list).
- `GET /partials/event-rate` returns uPlot-ready columnar JSON;
  HTMX swaps it into the chart-mount div every 15 s.
- `GET /partials/alerts` returns an HTML `<ul>` fragment (no
  `<html>` wrapper); HTMX swaps it into the alerts-list div every 30 s.
- `GET /alerts/{id}` is a full HTML detail page.

**uPlot is vendored** (`apps/api/src/watchdog_api/static/`), not
loaded from a CDN. `static/README.md` documents the source URL and
upgrade procedure.

## Tailwind Play CDN — trade-off recorded explicitly

We accept the **Tailwind Play CDN** (~3 MB browser-compiled dev build)
for this console. The audience is the ops team running wk-watchdog —
TTFP matters less than developer velocity, and the Play CDN avoids
us pulling a Node toolchain into a backend repo just for utility
classes. **This would be unacceptable for a customer-facing brand
surface**; production-skinned dashboards swap the CDN for a built
CSS file vendored next to uPlot.

## Alternatives considered

| Option                        | Why rejected                                                                                                                 |
| ----------------------------- | ---------------------------------------------------------------------------------------------------------------------------- |
| **Next.js / React SPA**       | Disqualified by the no-build-step constraint. The audience doesn't need an SPA.                                              |
| **Vue / Svelte**              | Same constraint as React; lighter but still needs a Node toolchain.                                                          |
| **Plotly Dash / Streamlit**   | Heavyweight Python frameworks that ship their own opinions about layout, hosting, and auth; would fight the FastAPI surface. |
| **Chart.js** instead of uPlot | ~200 KB vs uPlot's ~50 KB; uPlot is faster at our dataset size (1-minute buckets × 60 minutes).                              |
| **matplotlib PNG renders**    | No interactivity, no auto-refresh, no HTMX synergy. Acceptable for static reports, wrong for a live ops console.             |

## Consequences

| Positive                                                                             | Negative / accepted cost                                                                                                                                                     |
| ------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Page weight ~56 KB (HTML 4 + CSS 2 + JS 50); under the 100 KB Lighthouse sanity bar. | Tailwind Play CDN dev-build is fine for an ops console but inappropriate for a customer brand surface.                                                                       |
| Zero JS build step; no Node toolchain in the repo.                                   | The 4-panel chart bootstrap is a 30-line inline `<script>` block in the layout — would refactor to a vendored helper module if the dashboard grows beyond ~6 panels.         |
| Time-to-first-paint wins via SSR; no client-side hydration.                          | HTMX partials carry a thin coupling between server templates and client JS; a real SPA migration later is a rewrite (acceptable trade-off given the operator-only audience). |
| Supply-chain hygiene: uPlot vendored, not CDN.                                       | The bootstrap script depends on uPlot being globally available (`window.uPlot`) — slight coupling between the vendored JS file and the inline script.                        |

## References

- HTMX, _"High power tools for HTML"_. https://htmx.org/
- uPlot, _small, fast, performant time-series charts_. https://github.com/leeoniya/uPlot
- Tailwind Play CDN trade-off documented in the route handler
  module docstring: `apps/api/src/watchdog_api/routes/dashboard.py`.
- Page-weight assertion: `apps/api/tests/test_dashboard_routes.py::test_page_weight_under_100kb`.
