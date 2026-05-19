"""Loader for the hand-crafted severity-classification golden set.

The golden set lives next to this module as `golden_set.jsonl` so it
ships as a package resource (hatchling bundles all non-Python files
under `src/watchdog_core/` into the wheel).

Each line is one case:
    {
      "id": "001-low-info-mild-spike",
      "anomaly": { ... AnomalyWindow fields ... },
      "recent_messages": [ "...", ... ],
      "expected_severity": "low" | "medium" | "high" | "critical",
      "notes": "...",
      "adversarial": false  (optional; defaults False)
    }

Severity is judged by band, not exact match — see
`within_one_band(predicted, expected)`. LLM eval is noisy and a 90 %
within-one-band accuracy threshold is the right shape for a
gen-AI-judgment harness; exact-match would punish acceptable noise.
"""

from __future__ import annotations

import json
from importlib.resources import files

from pydantic import BaseModel, ConfigDict

from watchdog_core.domain.models import AnomalyWindow
from watchdog_core.genai.severity_classifier import SeverityBand

_RESOURCE_PKG = "watchdog_core.genai.eval"
_GOLDEN_FILE = "golden_set.jsonl"

_BANDS: tuple[SeverityBand, ...] = ("low", "medium", "high", "critical")
_BAND_INDEX: dict[SeverityBand, int] = {band: i for i, band in enumerate(_BANDS)}


class GoldenCase(BaseModel):
    """One scored case in the golden set."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    anomaly: AnomalyWindow
    recent_messages: list[str]
    expected_severity: SeverityBand
    notes: str = ""
    adversarial: bool = False


def load_golden_set() -> list[GoldenCase]:
    raw = (files(_RESOURCE_PKG) / _GOLDEN_FILE).read_text(encoding="utf-8")
    cases: list[GoldenCase] = []
    for raw_line in raw.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        cases.append(GoldenCase.model_validate(json.loads(line)))
    return cases


def band_distance(a: SeverityBand, b: SeverityBand) -> int:
    return abs(_BAND_INDEX[a] - _BAND_INDEX[b])


def within_one_band(predicted: SeverityBand, expected: SeverityBand) -> bool:
    return band_distance(predicted, expected) <= 1
