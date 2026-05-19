"""Single source of truth for the SDK version.

Lives in a leaf module (no SDK-internal imports) so that other SDK
modules can `from watchdog_sdk._version import __version__` without
creating a circular import through `__init__.py`. The package-level
re-export at `watchdog_sdk.__version__` continues to work for
external consumers.
"""

from __future__ import annotations

__version__ = "0.1.0"
