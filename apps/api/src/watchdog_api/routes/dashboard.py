"""Server-rendered ops console for wk-watchdog.

Choice rationale
----------------
We render server-side with **Jinja2 + HTMX swaps + uPlot** for the
one time-series chart on the page. This is a tools repo, not a
customer brand surface — we optimise for:

  * **Time-to-first-paint.** No JS framework bundle to download.
    The page is fully renderable from the HTML the server returns.
  * **Zero JS build step.** We do not maintain a Node toolchain in
    this repo; HTMX + a small inline bootstrap covers the
    interactivity we need.
  * **Backend-driven state.** The data lives in SQLite; HTMX swaps
    are the right size of tool — anything bigger (React, Vue) is
    over-budget for an internal console.
  * **Supply chain.** uPlot is **vendored** in
    `apps/api/src/watchdog_api/static/` (see `static/README.md`),
    NOT loaded from a runtime CDN that could be hijacked.

The one CDN dependency we accept is **Tailwind Play CDN**. Honest
trade-off: Play CDN ships a ~3 MB development build that compiles
classes in the browser, which would be unacceptable for a customer-
facing brand experience. The audience for THIS console is the
ops team running wk-watchdog — TTFP matters less than dev velocity,
and we avoid pulling Tailwind's build pipeline into a backend repo.
A production-skinned dashboard replaces Play CDN with a built CSS
file vendored next to uPlot.

Alternatives considered:
  * Next.js / React SPA — disqualified by the no-build-step constraint.
  * Chart.js — ~200 KB; uPlot is ~50 KB and faster at our dataset size.
  * matplotlib PNG renders — no interactivity, no auto-refresh.

The `test_dashboard_routes.test_page_weight_under_100kb` test
sanity-checks the total payload (HTML + JS + CSS, excluding Tailwind
CDN) is under 100 KB.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from watchdog_api.dependencies import DbDep
from watchdog_api.schemas.errors import ErrorResponse
from watchdog_core.persistence.repositories import AlertRepository, LogEventRepository

_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))

router = APIRouter(tags=["dashboard"])


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, db: DbDep) -> HTMLResponse:
    log_repo = LogEventRepository(db)
    alert_repo = AlertRepository(db)

    summary = await log_repo.summary_last_24h()
    alerts_by_severity = await alert_repo.count_by_severity_last_24h()

    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "summary": summary,
            "alerts_by_severity": alerts_by_severity,
        },
    )


@router.get("/partials/event-rate")
async def event_rate_partial(db: DbDep) -> JSONResponse:
    """uPlot-ready JSON `{ts: [unix...], counts: [int...]}` for last 60 min.

    HTMX swaps the response body into the chart-mount div; the inline
    bootstrap in `dashboard.html` parses the JSON and feeds it to uPlot.
    """
    log_repo = LogEventRepository(db)
    data = await log_repo.rate_per_minute(minutes=60)
    return JSONResponse(content=data)


@router.get("/partials/alerts", response_class=HTMLResponse)
async def alerts_partial(request: Request, db: DbDep) -> HTMLResponse:
    """HTML fragment (no `<html>`/`<body>` wrapper) — HTMX-swapped into #alerts-list."""
    alert_repo = AlertRepository(db)
    alerts = await alert_repo.list_recent(limit=20)
    return templates.TemplateResponse(
        request=request,
        name="partials/alerts.html",
        context={"alerts": alerts},
    )


@router.get(
    "/alerts/{alert_id}",
    response_class=HTMLResponse,
    responses={
        # `response_class=HTMLResponse` would otherwise propagate the
        # `text/html` content-type to ALL declared responses; the 404
        # body is actually JSON (FastAPI's default HTTPException
        # serialiser), so we override `content` explicitly. Without
        # this override schemathesis reports an Undocumented
        # Content-Type for the 404 path.
        status.HTTP_404_NOT_FOUND: {
            "description": "No alert exists with the given UUID.",
            "content": {
                "application/json": {
                    "schema": ErrorResponse.model_json_schema(),
                },
            },
        },
    },
)
async def alert_detail(
    request: Request,
    alert_id: UUID,
    db: DbDep,
) -> HTMLResponse:
    alert_repo = AlertRepository(db)
    alert = await alert_repo.get_by_id(alert_id)
    if alert is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="alert not found",
        )

    cursor = await db.execute(
        "SELECT id, attempts, last_attempt_at, next_attempt_at, status"
        " FROM webhook_outbox WHERE alert_id = ?"
        " ORDER BY id DESC",
        (str(alert_id),),
    )
    rows = await cursor.fetchall()
    await cursor.close()
    outbox_rows: list[dict[str, Any]] = [
        {
            "id": int(row[0]),
            "attempts": int(row[1]),
            "last_attempt_at": row[2],
            "next_attempt_at": row[3],
            "status": row[4],
        }
        for row in rows
    ]

    return templates.TemplateResponse(
        request=request,
        name="alert_detail.html",
        context={"alert": alert, "outbox_rows": outbox_rows},
    )
