"""LLM-backed severity classifier with structured-output (tool_use) + fallback.

Contract
--------
`SeverityClassifier.classify(anomaly, recent_messages) -> SeverityDecision`

Happy path:
  1. Build a versioned prompt from `prompts/severity_v1.md`.
  2. Call Anthropic's Messages API with `tools=[record_severity]` and
     `tool_choice={"type": "tool", "name": "record_severity"}` — the
     model is forced to emit a structured `tool_use` block.
  3. Parse the block's `input` into `SeverityDecision`.
  4. Record token usage in the `CostGuard` (prometheus counter +
     sliding-window cap).

Degradations (each visible via the `model` field of the returned
SeverityDecision — we never silently fall back):
  * Cost cap exceeded BEFORE the call → rule-based fallback,
    `model="rule-based-fallback"`, no API call made.
  * API error or timeout > 2 s → retry once.
  * Retry also fails → rule-based fallback.
  * API returns text instead of `tool_use` → treated as a soft error;
    one retry; on second failure → rule-based fallback.

Safety
------
The prompt template explicitly instructs the model to treat log
message bodies as DATA, not commands. The `test_severity_eval`
adversarial case verifies that a prompt-injection payload inside a
log message does not elevate the severity decision.
"""

from __future__ import annotations

import asyncio
import logging
import time
from importlib.resources import files
from typing import Any, Literal

import anthropic
from anthropic import AsyncAnthropic
from pydantic import BaseModel, ConfigDict, Field

from watchdog_core.domain.models import AnomalyWindow
from watchdog_core.genai.cost_guard import CostGuard

logger = logging.getLogger(__name__)

_DEFAULT_MODEL = "claude-3-5-haiku-20241022"
_DEFAULT_TIMEOUT_S = 2.0
_TOOL_NAME = "record_severity"
_FALLBACK_MODEL_TAG = "rule-based-fallback"
_PROMPT_PACKAGE = "watchdog_core.genai.prompts"
_PROMPT_FILE_V1 = "severity_v1.md"
_MAX_RECENT_MESSAGES = 5
# Rule-based-fallback z-score thresholds — picked to match the brief's spec
# (level=CRITICAL→critical, z>10→high, z>5→medium, else low).
_Z_HIGH_THRESHOLD = 10.0
_Z_MEDIUM_THRESHOLD = 5.0

SeverityBand = Literal["low", "medium", "high", "critical"]


class SeverityDecision(BaseModel):
    """The structured output of the classifier (tool_use input)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    severity: SeverityBand
    reasoning: str = Field(min_length=1, max_length=2000)
    confidence: float = Field(ge=0.0, le=1.0)
    suggested_action: str = Field(min_length=1, max_length=1000)
    model: str = Field(min_length=1)
    latency_ms: int = Field(ge=0)


SEVERITY_TOOL: dict[str, Any] = {
    "name": _TOOL_NAME,
    "description": (
        "Record the severity classification for an anomaly window. "
        "Call this exactly once per anomaly."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "severity": {
                "type": "string",
                "enum": ["low", "medium", "high", "critical"],
                "description": "Severity band for this anomaly.",
            },
            "reasoning": {
                "type": "string",
                "description": "Concise rationale grounded in the anomaly facts.",
            },
            "confidence": {
                "type": "number",
                "minimum": 0.0,
                "maximum": 1.0,
                "description": "Calibrated confidence in the chosen severity (0-1).",
            },
            "suggested_action": {
                "type": "string",
                "description": "One concrete next step.",
            },
        },
        "required": ["severity", "reasoning", "confidence", "suggested_action"],
    },
}


class _ModelReturnedTextError(RuntimeError):
    """The model emitted text instead of calling the tool — soft retry signal."""


def _load_prompt_template(filename: str = _PROMPT_FILE_V1) -> str:
    return (files(_PROMPT_PACKAGE) / filename).read_text(encoding="utf-8")


def _format_recent_messages(messages: list[str]) -> str:
    if not messages:
        return "(none)"
    head = messages[:_MAX_RECENT_MESSAGES]
    return "\n".join(f"- {m}" for m in head)


def _build_prompt(
    template: str,
    anomaly: AnomalyWindow,
    recent_messages: list[str],
) -> str:
    return template.format(
        service=anomaly.service,
        level=anomaly.level,
        window_start=anomaly.window_start.isoformat(),
        window_end=anomaly.window_end.isoformat(),
        count=anomaly.count,
        baseline_mean=anomaly.baseline_mean,
        baseline_stddev=anomaly.baseline_stddev,
        z_score=anomaly.z_score,
        recent_messages_block=_format_recent_messages(recent_messages),
    )


def _rule_based_decision(
    anomaly: AnomalyWindow, *, latency_ms: int, reason: str
) -> SeverityDecision:
    """Deterministic fallback, fully observable via `model` tag."""
    if anomaly.level == "CRITICAL":
        severity: SeverityBand = "critical"
        action = "Page on-call immediately."
    elif anomaly.z_score > _Z_HIGH_THRESHOLD:
        severity = "high"
        action = "Investigate within 30 minutes."
    elif anomaly.z_score > _Z_MEDIUM_THRESHOLD:
        severity = "medium"
        action = "Acknowledge in incident channel and observe."
    else:
        severity = "low"
        action = "Track in dashboard; ignore unless it persists."

    return SeverityDecision(
        severity=severity,
        reasoning=(
            f"Rule-based fallback ({reason}). "
            f"level={anomaly.level}, z_score={anomaly.z_score:.2f}, count={anomaly.count}."
        ),
        confidence=0.6,
        suggested_action=action,
        model=_FALLBACK_MODEL_TAG,
        latency_ms=latency_ms,
    )


def _elapsed_ms(start: float) -> int:
    return int((time.monotonic() - start) * 1000)


class SeverityClassifier:
    """LLM-first classifier with deterministic fallback.

    Construct with `client=...` in tests to inject a mock-friendly
    AsyncAnthropic; or pass `api_key=...` (or set `ANTHROPIC_API_KEY`).
    """

    def __init__(  # noqa: PLR0913 — DI seam: api_key/client/model/timeout/cost_guard/prompt_template are all legitimate injection points
        self,
        *,
        api_key: str | None = None,
        client: AsyncAnthropic | None = None,
        model: str = _DEFAULT_MODEL,
        timeout: float = _DEFAULT_TIMEOUT_S,
        cost_guard: CostGuard | None = None,
        prompt_template: str | None = None,
    ) -> None:
        self._model = model
        self._timeout = timeout
        self._cost_guard = cost_guard or CostGuard()
        self._prompt = prompt_template or _load_prompt_template()
        if client is not None:
            self._client = client
        else:
            self._client = AsyncAnthropic(
                api_key=api_key or "sk-no-key-configured",
                timeout=timeout,
            )

    async def classify(
        self,
        anomaly: AnomalyWindow,
        recent_messages: list[str],
    ) -> SeverityDecision:
        start = time.monotonic()

        if self._cost_guard.over_cap():
            logger.warning(
                "genai cost cap exceeded; forcing rule-based fallback",
                extra={"service": anomaly.service, "level": anomaly.level},
            )
            return _rule_based_decision(
                anomaly,
                latency_ms=_elapsed_ms(start),
                reason="cost cap exceeded",
            )

        for attempt in (1, 2):
            try:
                return await self._call_api(anomaly, recent_messages, start)
            except (
                TimeoutError,
                anthropic.APIError,
                anthropic.APIConnectionError,
                anthropic.APITimeoutError,
                _ModelReturnedTextError,
            ) as exc:
                logger.warning(
                    "genai classify attempt failed",
                    extra={"attempt": attempt, "error": str(exc)},
                )

        return _rule_based_decision(
            anomaly,
            latency_ms=_elapsed_ms(start),
            reason="api retries exhausted",
        )

    async def _call_api(
        self,
        anomaly: AnomalyWindow,
        recent_messages: list[str],
        start: float,
    ) -> SeverityDecision:
        prompt = _build_prompt(self._prompt, anomaly, recent_messages)

        # Anthropic SDK uses TypedDict ToolParam / MessageParam shapes; we
        # pass plain dicts matching the wire schema. mypy can't unify the
        # TypedDict against our literal dicts here without a verbose cast,
        # and switching to the SDK's TypedDicts would re-export them
        # outward. The wire shape is correct (tested by respx mocks).
        response = await asyncio.wait_for(
            self._client.messages.create(  # type: ignore[call-overload]
                model=self._model,
                max_tokens=512,
                tools=[SEVERITY_TOOL],
                tool_choice={"type": "tool", "name": _TOOL_NAME},
                messages=[{"role": "user", "content": prompt}],
            ),
            timeout=self._timeout,
        )

        usage = response.usage
        self._cost_guard.record(
            input_tokens=getattr(usage, "input_tokens", 0) or 0,
            output_tokens=getattr(usage, "output_tokens", 0) or 0,
            model=self._model,
        )

        for block in response.content:
            if block.type == "tool_use" and block.name == _TOOL_NAME:
                payload = dict(block.input) if isinstance(block.input, dict) else {}
                return SeverityDecision(
                    severity=payload.get("severity", "low"),
                    reasoning=payload.get("reasoning", ""),
                    confidence=float(payload.get("confidence", 0.0)),
                    suggested_action=payload.get("suggested_action", ""),
                    model=self._model,
                    latency_ms=_elapsed_ms(start),
                )

        msg = "model returned no record_severity tool_use block"
        raise _ModelReturnedTextError(msg)
