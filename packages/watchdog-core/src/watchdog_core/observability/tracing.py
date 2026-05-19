"""OpenTelemetry tracing helpers used by watchdog_core code.

We keep these in `watchdog_core` (rather than `watchdog_api`) because
the repositories and services that need to emit spans live here, and
they MUST NOT depend on FastAPI / the application package.

`traced_execute` wraps an aiosqlite `Connection.execute()` in a span
because aiosqlite's auto-instrumentation is incomplete (it traces
the connection lifecycle but not the individual statements). The
span carries OpenTelemetry semantic-convention attributes:
  * db.system = "sqlite"
  * db.statement = <first 200 chars of SQL>
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from opentelemetry import trace

if TYPE_CHECKING:
    import aiosqlite

_DB_TRACER = trace.get_tracer("watchdog_core.persistence")
_MAX_STMT_LENGTH = 200


async def traced_execute(
    conn: aiosqlite.Connection,
    sql: str,
    params: tuple[Any, ...] | None = None,
) -> aiosqlite.Cursor:
    """Execute a SQL statement inside an OTel span.

    Returns the aiosqlite cursor exactly like `conn.execute()` does.
    """
    with _DB_TRACER.start_as_current_span("sqlite.execute") as span:
        span.set_attribute("db.system", "sqlite")
        span.set_attribute("db.statement", sql[:_MAX_STMT_LENGTH])
        return await conn.execute(sql, params or ())
