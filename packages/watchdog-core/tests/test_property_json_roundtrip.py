"""Property test: LogEvent.attributes survive the SQLite TEXT-as-JSON round-trip.

This guards against silent data loss in `LogEventRepository.insert` /
`_row_to_event`. The repository serialises `attributes` with
`json.dumps(..., sort_keys=True)` and reads back via `json.loads(...)`.
If a future change introduces a custom encoder or a column-truncation
bug, this test fails on the very first Hypothesis example.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import aiosqlite
import pytest_asyncio

# Builders live alongside the tests; pytest puts tests/ on sys.path.
from builders import make_log_event  # type: ignore[import-not-found]
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from watchdog_core.persistence.migrations import apply_migrations
from watchdog_core.persistence.repositories import LogEventRepository


@pytest_asyncio.fixture
async def conn(tmp_path: Path) -> AsyncIterator[aiosqlite.Connection]:
    db_path = tmp_path / "property.sqlite"
    async with aiosqlite.connect(str(db_path)) as c:
        await apply_migrations(c)
        yield c


# Limit the value alphabet to printable text + safe punctuation so the
# property exercises the JSON encoder without spending Hypothesis budget
# on unicode-edge-case territory (those are handled by Python's stdlib
# json module and aren't what THIS test is trying to falsify).
_printable_text = st.text(
    alphabet=st.characters(
        whitelist_categories=("Ll", "Lu", "Nd", "Pc", "Po", "Zs"),
        min_codepoint=32,
        max_codepoint=126,
    ),
    min_size=1,
    max_size=64,
)
_attrs_strategy = st.dictionaries(
    keys=_printable_text,
    values=st.text(
        alphabet=st.characters(min_codepoint=32, max_codepoint=126),
        min_size=0,
        max_size=256,
    ),
    min_size=0,
    max_size=8,
)


@given(attrs=_attrs_strategy)
@settings(
    deadline=None,
    max_examples=30,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
async def test_log_event_attributes_roundtrip_through_sqlite(
    conn: aiosqlite.Connection,
    attrs: dict[str, str],
) -> None:
    repo = LogEventRepository(conn)
    event = make_log_event(service="prop-test", attributes=attrs)
    await repo.insert(event)
    recent = await repo.list_recent("prop-test", limit=1)
    assert len(recent) == 1
    assert recent[0].attributes == attrs
