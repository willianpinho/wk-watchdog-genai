"""Liveness and readiness routes.

`/healthz` (liveness) — the process is up. ALWAYS 200 as long as we
    can respond. Liveness probes detect "the process is wedged and
    should be restarted". They MUST NOT depend on downstream services
    (a healthy app with an unhealthy database is still *alive*;
    restarting won't fix the DB).

`/readyz` (readiness) — the app can serve traffic. SQLite reachable,
    schema migrated. Failing readiness removes the pod from the LB
    but does NOT trigger a restart. Returns 503 on failure.
"""

from __future__ import annotations

from typing import Any

import aiosqlite
from fastapi import APIRouter, HTTPException, status

from watchdog_api.dependencies import DbDep

router = APIRouter(tags=["health"])


@router.get("/healthz", status_code=status.HTTP_200_OK)
async def liveness() -> dict[str, str]:
    """Liveness — always 200 if the process can respond."""
    return {"status": "alive"}


@router.get("/readyz")
async def readiness(db: DbDep) -> dict[str, Any]:
    """Readiness — DB reachable + schema migrated."""
    try:
        cursor = await db.execute("SELECT MAX(version) FROM schema_migrations")
        row = await cursor.fetchone()
        await cursor.close()
    except aiosqlite.Error as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"db error: {exc}",
        ) from exc

    if row is None or row[0] is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="schema not migrated",
        )
    return {"status": "ready", "schema_version": int(row[0])}
