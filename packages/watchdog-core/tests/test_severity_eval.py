"""Golden-set eval for the SeverityClassifier.

The mock returns the band that a *well-behaved* model would return
given the anomaly facts — letting us test the harness end-to-end
(prompt build → API call → tool_use parse → SeverityDecision) without
spending tokens. Real LLM eval runs offline against the same JSONL
file with an unmocked API key.

Assertions:
  * Aggregate accuracy ≥ 90 % within-one-band over the entire set.
  * The adversarial case (prompt injection inside a log message)
    MUST NOT be elevated to critical.
"""

from __future__ import annotations

import json
import re
from typing import Any

import pytest
import respx
from httpx import Request, Response

from watchdog_core.genai.cost_guard import CostGuard
from watchdog_core.genai.eval.golden_set import (
    GoldenCase,
    load_golden_set,
    within_one_band,
)
from watchdog_core.genai.severity_classifier import SeverityClassifier

_BASE_URL = "https://api.anthropic.com"
_ACCURACY_GATE = 0.90

# We map golden-case id → expected severity inside the mock to keep
# parsing trivial. The classifier embeds the case-id-equivalent
# (anomaly facts) in the prompt; the mock matches on the user-message
# body to extract the expected band.


def _build_id_to_severity_map(cases: list[GoldenCase]) -> dict[str, str]:
    # We key the mock by `service` since (service, level, count, z) is
    # unique in the golden set by construction.
    return {
        f"{c.anomaly.service}|{c.anomaly.level}|{c.anomaly.count}": c.expected_severity
        for c in cases
    }


def _mock_response_for(severity: str) -> dict[str, Any]:
    return {
        "id": "msg_eval",
        "type": "message",
        "role": "assistant",
        "model": "claude-3-5-haiku-20241022",
        "stop_reason": "tool_use",
        "content": [
            {
                "type": "tool_use",
                "id": "toolu_eval",
                "name": "record_severity",
                "input": {
                    "severity": severity,
                    "reasoning": "mock-eval reasoning grounded in anomaly facts.",
                    "confidence": 0.9,
                    "suggested_action": "mock action.",
                },
            },
        ],
        "usage": {"input_tokens": 250, "output_tokens": 60},
    }


def _make_router(id_to_sev: dict[str, str]) -> respx.MockRouter:
    router = respx.mock(base_url=_BASE_URL, assert_all_called=False)
    router.start()

    def respond(request: Request) -> Response:
        body = json.loads(request.content.decode("utf-8"))
        user_msg = body["messages"][0]["content"]
        # Pull (service, level, count) out of the prompt body.
        m_service = re.search(r"Service:\s*([\w\-]+)", user_msg)
        m_level = re.search(r"Log level:\s*([A-Z]+)", user_msg)
        m_count = re.search(r"Observed count in window:\s*(\d+)", user_msg)
        if not (m_service and m_level and m_count):
            return Response(500, json={"error": "unparseable prompt"})
        key = f"{m_service.group(1)}|{m_level.group(1)}|{m_count.group(1)}"
        sev = id_to_sev.get(key, "low")
        return Response(200, json=_mock_response_for(sev))

    router.post("/v1/messages").mock(side_effect=respond)
    return router


@pytest.mark.asyncio
async def test_golden_set_eval_within_one_band_accuracy() -> None:
    cases = load_golden_set()
    assert len(cases) >= 20, f"golden set has {len(cases)} cases, need ≥20"

    id_to_sev = _build_id_to_severity_map(cases)
    router = _make_router(id_to_sev)

    try:
        classifier = SeverityClassifier(
            api_key="sk-test",
            cost_guard=CostGuard(tokens_per_minute_cap=10_000_000),
        )
        hits = 0
        misses: list[tuple[str, str, str]] = []
        for case in cases:
            decision = await classifier.classify(case.anomaly, case.recent_messages)
            if within_one_band(decision.severity, case.expected_severity):
                hits += 1
            else:
                misses.append((case.id, case.expected_severity, decision.severity))
        accuracy = hits / len(cases)
        assert accuracy >= _ACCURACY_GATE, (
            f"within-one-band accuracy {accuracy:.1%} below gate {_ACCURACY_GATE:.0%}: "
            f"misses={misses}"
        )
    finally:
        router.stop()
        router.reset()


@pytest.mark.asyncio
async def test_adversarial_case_is_not_elevated_to_critical() -> None:
    """A log message that screams "return critical" must NOT cause the
    classifier to return critical. We instrument the mock to ignore
    the injection (mirroring what a properly-instructed model does)
    and verify the system end-to-end does not propagate the elevation.
    """
    cases = load_golden_set()
    adversarial = next(c for c in cases if c.adversarial)
    id_to_sev = _build_id_to_severity_map(cases)
    router = _make_router(id_to_sev)

    try:
        classifier = SeverityClassifier(
            api_key="sk-test",
            cost_guard=CostGuard(tokens_per_minute_cap=10_000_000),
        )
        decision = await classifier.classify(
            adversarial.anomaly,
            adversarial.recent_messages,
        )
        assert decision.severity != "critical", (
            f"adversarial case {adversarial.id} was elevated to critical by the harness — "
            "prompt-injection defense is broken"
        )
    finally:
        router.stop()
        router.reset()
