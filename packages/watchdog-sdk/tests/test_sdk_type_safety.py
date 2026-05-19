"""Standalone mypy --strict check on the SDK source tree.

Subprocess-runs mypy against ONLY the SDK package — separate from the
workspace-level `make type` — so we know the SDK passes type-check
in isolation. If a future change introduces a `from watchdog_api import …`
cycle (the SDK MUST NOT depend on the api), this test breaks the build
even before pyright/mypy run on the rest of the repo.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SDK_SRC = _REPO_ROOT / "packages" / "watchdog-sdk" / "src" / "watchdog_sdk"


@pytest.mark.slow
def test_sdk_passes_mypy_strict_standalone() -> None:
    assert _SDK_SRC.exists(), f"SDK source root missing: {_SDK_SRC}"

    # S603/S607 ignores are at the per-file level in pyproject.toml.
    result = subprocess.run(
        ["uv", "run", "mypy", "--strict", str(_SDK_SRC)],
        check=False,
        capture_output=True,
        cwd=_REPO_ROOT,
        timeout=120,
    )
    stdout = result.stdout.decode("utf-8", errors="replace")
    stderr = result.stderr.decode("utf-8", errors="replace")
    assert result.returncode == 0, (
        f"mypy --strict failed on the SDK source:\n"
        f"--- stdout ---\n{stdout}\n"
        f"--- stderr ---\n{stderr}"
    )


def test_sdk_does_not_import_watchdog_api() -> None:
    """The SDK must not couple to the server-side app package.

    Greps every `.py` file in the SDK source tree for `watchdog_api`;
    if any matches, the SDK has accidentally pulled the api into its
    dependency surface and the next contributor will inherit the bug.
    """
    offenders: list[Path] = []
    for path in _SDK_SRC.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "watchdog_api" in text:
            offenders.append(path)
    assert not offenders, (
        f"SDK files importing watchdog_api break the SDK→api invariant: "
        f"{[p.relative_to(_REPO_ROOT) for p in offenders]}"
    )
