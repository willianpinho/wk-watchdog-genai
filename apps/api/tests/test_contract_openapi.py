"""OpenAPI schema-conformance smoke test.

Full contract testing (every operation x every parameter x every status
code) is delegated to the `schemathesis` CLI — see the `test-contract`
target in the Makefile and the documented CI invocation:

    schemathesis run http://localhost:8000/openapi.json --checks all --hypothesis-deadline=2000

That command is too slow (and too noisy) to run on every developer
laptop, so it lives in CI. THIS pytest test is the cheap online
smoke: the schema is well-formed, the headline operations are
registered, and the response models match what the routes actually
return for a happy POST.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from watchdog_api.config import Settings
from watchdog_api.main import create_app


@pytest_asyncio.fixture
async def client(tmp_path: Path) -> AsyncIterator[AsyncClient]:
    db_path = tmp_path / "contract.sqlite"
    settings = Settings(db_url=f"sqlite+aiosqlite:///{db_path}", otel_enabled=False)
    app = create_app(settings)
    async with (
        app.router.lifespan_context(app),
        AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as c,
    ):
        yield c


async def test_openapi_schema_is_well_formed(client: AsyncClient) -> None:
    r = await client.get("/openapi.json")
    assert r.status_code == 200
    schema = r.json()
    assert schema["openapi"].startswith("3."), schema["openapi"]
    assert "paths" in schema
    # Headline operations the dashboard + downstream consumers rely on.
    # `/metrics` is deliberately excluded via `include_in_schema=False` —
    # it's a Prometheus endpoint, not a JSON API consumer surface.
    for expected_path in ("/v1/events", "/healthz", "/readyz"):
        assert expected_path in schema["paths"], (
            f"missing {expected_path} from OpenAPI schema; saw {list(schema['paths'].keys())}"
        )


async def test_post_events_response_conforms_to_schema(client: AsyncClient) -> None:
    """The 202 body matches the BatchIngestResponse schema keys."""
    payload = {
        "events": [
            {
                "ts": "2026-05-19T12:00:00+00:00",
                "service": "contract-test",
                "level": "INFO",
                "message": "shape check",
                "attributes": {},
            },
        ],
    }
    r = await client.post("/v1/events", json=payload)
    assert r.status_code == 202, r.text
    body = r.json()
    assert set(body.keys()) == {"accepted", "rejected"}
    assert isinstance(body["accepted"], int)
    assert isinstance(body["rejected"], list)
