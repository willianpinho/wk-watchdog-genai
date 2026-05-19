"""Repo-root conftest — auto-marks tests by file location.

We default every test to the `unit` marker so the default
`pytest -m "unit or integration"` selection picks it up without
each test author having to remember to decorate. Tests in
file names listed in `_INTEGRATION_FILES` are flipped to
`integration` — they spin up an ASGI app or open a real file-DB.

Test authors can override by adding an explicit
`@pytest.mark.{unit,integration,contract,slow}` decorator; we
only auto-mark tests that carry none of those.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_INTEGRATION_FILES = {
    # apps/api/tests/
    "test_e2e_alerting.py",
    "test_otel_smoke.py",
    "test_metrics_endpoint.py",
    "test_dashboard_routes.py",
    "test_ingestion_route.py",
    # packages/watchdog-core/tests/
    "test_repositories.py",
    "test_aggregation_queries.py",
    "test_outbox_worker.py",
    "test_outbox_dead_letter.py",
    "test_outbox_crash_safety.py",
}

_OWN_MARKERS = ("unit", "integration", "contract", "slow")


def pytest_collection_modifyitems(
    config: pytest.Config,
    items: list[pytest.Item],
) -> None:
    _ = config
    for item in items:
        if any(item.get_closest_marker(name) for name in _OWN_MARKERS):
            continue
        filename = Path(item.fspath).name
        if filename in _INTEGRATION_FILES:
            item.add_marker(pytest.mark.integration)
        else:
            item.add_marker(pytest.mark.unit)
