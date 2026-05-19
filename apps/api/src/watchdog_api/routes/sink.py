"""Demo + integration-test webhook sink.

`POST /v1/_sink` is the receiver side of the watchdog's webhook
contract. It:
  * Verifies the `X-Watchdog-Signature` header against the
    configured shared secret using `verify_signature`.
  * Returns 401 on signature mismatch / expired timestamp.
  * Returns 200 + `{"received": true, "alert_id": ...}` on success.
  * Stashes the parsed payload in `request.app.state.sink_received`
    so tests (and the demo dashboard) can inspect deliveries.

Production deployments should disable this route or move it behind
an internal auth gate. The leading underscore in `/v1/_sink` is a
soft-deprecated-when-not-in-test convention used to flag this.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status

from watchdog_core.alerting.webhook_dispatcher import verify_signature

router = APIRouter(prefix="/v1", tags=["sink"])


@router.post("/_sink")
async def sink_receiver(request: Request) -> dict[str, Any]:
    """Verify HMAC + record + 200, or reject with 401."""
    body = await request.body()
    signature = request.headers.get("X-Watchdog-Signature", "")
    secret = request.app.state.settings.webhook_secret

    if not verify_signature(body, signature, secret):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid signature",
        )

    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"invalid JSON body: {exc}",
        ) from exc

    received: list[dict[str, Any]] = getattr(request.app.state, "sink_received", [])
    received.append(payload)
    request.app.state.sink_received = received

    return {
        "received": True,
        "alert_id": payload.get("alert_id"),
        "size": len(body),
    }
