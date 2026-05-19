"""Shared error envelope for non-2xx OpenAPI responses.

The shape mirrors `fastapi.exceptions.HTTPException(detail=...)` which
FastAPI's default exception handler serialises as
`{"detail": "<message>"}`. Adding an optional `code` field lets the
SDK and dashboard branch on machine-readable error codes when those
exist (Turn 12+ ADR), without forcing every error site to populate it.

Frozen + extra=forbid so the contract is enforced as a schema fact;
schemathesis (`run --checks all`) verifies our routes return bodies
that conform to this shape every time we ship a 4xx/5xx.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class ErrorResponse(BaseModel):
    """OpenAPI response model for documented 4xx / 5xx paths."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    detail: str
    code: str | None = None
