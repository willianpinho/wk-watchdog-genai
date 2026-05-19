"""Integration tests for POST /v1/events + the health routes.

Uses httpx.AsyncClient + ASGITransport with the FastAPI lifespan
manually run, so migrations are applied against the temp DB before
any request fires.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from httpx import AsyncClient


async def test_healthz_returns_200(client: AsyncClient) -> None:
    r = await client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "alive"}


async def test_readyz_returns_200_after_migrations(client: AsyncClient) -> None:
    r = await client.get("/readyz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ready"
    assert body["schema_version"] >= 1


async def test_post_events_happy_returns_202(client: AsyncClient) -> None:
    payload = {
        "events": [
            {
                "ts": "2026-05-19T12:00:00+00:00",
                "service": "api",
                "level": "INFO",
                "message": "hello",
                "attributes": {},
            },
        ],
    }
    r = await client.post("/v1/events", json=payload)
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["accepted"] == 1
    assert body["rejected"] == []


async def test_post_events_empty_batch_returns_422(client: AsyncClient) -> None:
    r = await client.post("/v1/events", json={"events": []})
    assert r.status_code == 422


async def test_post_events_oversize_batch_returns_422(client: AsyncClient) -> None:
    events = [
        {
            "ts": "2026-05-19T12:00:00+00:00",
            "service": "api",
            "level": "INFO",
            "message": f"m-{i}",
            "attributes": {},
        }
        for i in range(1001)
    ]
    r = await client.post("/v1/events", json={"events": events})
    assert r.status_code == 422


async def test_post_events_dedupes_within_window(client: AsyncClient) -> None:
    base = (datetime.now(UTC) - timedelta(seconds=2)).isoformat()
    later = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    payload = {
        "events": [
            {
                "ts": base,
                "service": "api",
                "level": "ERROR",
                "message": "boom",
                "attributes": {},
            },
            {
                "ts": later,
                "service": "api",
                "level": "ERROR",
                "message": "boom",
                "attributes": {},
            },
        ],
    }
    r = await client.post("/v1/events", json=payload)
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["accepted"] == 1
    assert len(body["rejected"]) == 1
    assert body["rejected"][0]["index"] == 1
    assert body["rejected"][0]["reason"] == "duplicate"


async def test_post_events_rejects_event_older_than_24h(client: AsyncClient) -> None:
    old = (datetime.now(UTC) - timedelta(hours=25)).isoformat()
    payload = {
        "events": [
            {
                "ts": old,
                "service": "api",
                "level": "ERROR",
                "message": "ancient",
                "attributes": {},
            },
        ],
    }
    r = await client.post("/v1/events", json=payload)
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["accepted"] == 0
    assert len(body["rejected"]) == 1
    assert "old" in body["rejected"][0]["reason"]


async def test_post_events_rejects_extra_fields(client: AsyncClient) -> None:
    payload = {
        "events": [
            {
                "ts": "2026-05-19T12:00:00+00:00",
                "service": "api",
                "level": "INFO",
                "message": "hello",
                "attributes": {},
                "uninvited": "field",
            },
        ],
    }
    r = await client.post("/v1/events", json=payload)
    assert r.status_code == 422


async def test_post_events_then_readyz_round_trip(client: AsyncClient) -> None:
    """Round-trip: ingest → readyz still 200 → schema version reported."""
    payload = {
        "events": [
            {
                "ts": "2026-05-19T12:00:00+00:00",
                "service": "api",
                "level": "INFO",
                "message": "round-trip",
                "attributes": {"k": "v"},
            },
        ],
    }
    post = await client.post("/v1/events", json=payload)
    assert post.status_code == 202

    ready = await client.get("/readyz")
    assert ready.status_code == 200
    assert ready.json()["status"] == "ready"
