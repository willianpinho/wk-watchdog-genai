"""Public model surface for the SDK.

We **re-export** the Pydantic models that already live in
`watchdog_core.domain` — single source of truth. `LogEventDraft`
is the wire-input shape; we alias it as `LogEventInput` to read
better at SDK call sites.

Dependency direction
--------------------
`watchdog-sdk` depends on `watchdog-core` (one-way). `watchdog-sdk`
must NEVER import from `watchdog-api`; the SDK is the public client
surface and pulling in the server-side FastAPI stack would make the
SDK heavy for the wrong reason.
"""

from __future__ import annotations

from watchdog_core.domain.models import (
    Alert,
    AlertSeverity,
    AnomalyWindow,
    LogEvent,
    LogLevel,
    WebhookStatus,
)
from watchdog_core.domain.models import (
    LogEventDraft as LogEventInput,
)

__all__ = [
    "Alert",
    "AlertSeverity",
    "AnomalyWindow",
    "LogEvent",
    "LogEventInput",
    "LogLevel",
    "WebhookStatus",
]
