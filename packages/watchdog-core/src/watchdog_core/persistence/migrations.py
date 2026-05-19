"""Versioned schema migrations for wk-watchdog.

Why we do NOT use Alembic
-------------------------
Alembic is the de-facto standard for SQLAlchemy-backed Postgres
projects, but:

  * The MVP database is SQLite, used directly through aiosqlite (no
    ORM). Alembic's strongest features (autogenerate from SQLAlchemy
    models, env.py-driven multi-engine setup) deliver no value to a
    raw-SQL aiosqlite stack.
  * SQLite has limited ALTER TABLE support. Any non-trivial future
    migration requires the table-rebuild dance (CREATE temp, INSERT
    SELECT, DROP, RENAME), which Alembic's autogenerate models poorly.
  * Pulling Alembic + alembic.ini + env.py into the dependency surface
    contradicts ADR-001's pragmatic-engineering principle (no ceremony
    without payoff).

A ~70-line idempotent runner is sufficient for the lifetime of the MVP.
When ADR-001's Postgres-migration trigger fires (sustained > 50 req/s
or multi-writer demand), we adopt Alembic at that time, which is also
when we will have ORM-level models for it to autogenerate from.

Idempotency
-----------
Migrations are an append-only `(version, filename)` list at module
level. The runner records applied versions in `schema_migrations`
inside the DB; re-running on a current DB is a no-op (returns 0).
Each file is applied via `executescript` and committed atomically.
"""

from __future__ import annotations

from datetime import UTC, datetime
from importlib.resources import files
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import aiosqlite

# Append-only, ordered. Filenames are package resources inside
# `watchdog_core.persistence` (bundled into the wheel by hatchling).
MIGRATIONS: list[tuple[int, str]] = [
    (1, "schema.sql"),
]

_RESOURCE_PKG = "watchdog_core.persistence"


async def apply_pragmas(conn: aiosqlite.Connection) -> None:
    """Enable WAL + foreign keys. Idempotent; safe on every connect."""
    await conn.execute("PRAGMA journal_mode = WAL")
    await conn.execute("PRAGMA foreign_keys = ON")
    await conn.commit()


async def _ensure_meta_table(conn: aiosqlite.Connection) -> None:
    await conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        " version INTEGER PRIMARY KEY,"
        " applied_at TEXT NOT NULL"
        ")",
    )
    await conn.commit()


async def _applied_versions(conn: aiosqlite.Connection) -> set[int]:
    cursor = await conn.execute("SELECT version FROM schema_migrations")
    rows = await cursor.fetchall()
    await cursor.close()
    return {int(r[0]) for r in rows}


def _load_sql(filename: str) -> str:
    return (files(_RESOURCE_PKG) / filename).read_text(encoding="utf-8")


async def apply_migrations(conn: aiosqlite.Connection) -> int:
    """Apply pending migrations. Returns the number applied (0 = up-to-date)."""
    await apply_pragmas(conn)
    await _ensure_meta_table(conn)

    already = await _applied_versions(conn)
    applied = 0

    for version, filename in sorted(MIGRATIONS):
        if version in already:
            continue
        sql = _load_sql(filename)
        await conn.executescript(sql)
        await conn.execute(
            "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
            (version, datetime.now(UTC).isoformat()),
        )
        await conn.commit()
        applied += 1

    return applied
