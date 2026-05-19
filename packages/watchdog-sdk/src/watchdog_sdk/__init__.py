"""watchdog-sdk — Python client for the wk-watchdog Observability API."""

# PEP 484 explicit re-exports — `import X as X` is how we tell ruff
# (and any other unused-import remover) that these are part of the
# public API. Aggressive formatters that strip F401 still see these
# as "used" because of the as-alias pattern.

from watchdog_sdk._version import __version__ as __version__
from watchdog_sdk.client import AsyncWatchdogClient as AsyncWatchdogClient
from watchdog_sdk.client import EventAck as EventAck
from watchdog_sdk.client import Rejection as Rejection
from watchdog_sdk.client import WatchdogClient as WatchdogClient
from watchdog_sdk.instrumentation import WatchdogHandler as WatchdogHandler
from watchdog_sdk.instrumentation import instrument_logging as instrument_logging
from watchdog_sdk.models import Alert as Alert
from watchdog_sdk.models import AlertSeverity as AlertSeverity
from watchdog_sdk.models import AnomalyWindow as AnomalyWindow
from watchdog_sdk.models import LogEvent as LogEvent
from watchdog_sdk.models import LogEventInput as LogEventInput
from watchdog_sdk.models import LogLevel as LogLevel
from watchdog_sdk.models import WebhookStatus as WebhookStatus
from watchdog_sdk.retries import RetryPolicy as RetryPolicy

__all__ = [
    "Alert",
    "AlertSeverity",
    "AnomalyWindow",
    "AsyncWatchdogClient",
    "EventAck",
    "LogEvent",
    "LogEventInput",
    "LogLevel",
    "Rejection",
    "RetryPolicy",
    "WatchdogClient",
    "WatchdogHandler",
    "WebhookStatus",
    "__version__",
    "instrument_logging",
]
