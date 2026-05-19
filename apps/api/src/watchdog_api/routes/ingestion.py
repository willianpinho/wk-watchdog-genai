"""POST /v1/events — batch ingest route.

Pydantic enforces the wire shape (1..1000 events per request, see
`BatchIngestRequest.events`). FastAPI returns 422 automatically on
oversized batches. The route does NOT contain business validation
beyond Pydantic — it dispatches to `IngestionService.ingest_batch`,
which owns the dedupe / age / normalization rules.
"""

from __future__ import annotations

from fastapi import APIRouter, status
from pydantic import BaseModel, ConfigDict, Field

from watchdog_api.dependencies import IngestionServiceDep
from watchdog_api.schemas.errors import ErrorResponse
from watchdog_core.domain.models import LogEventDraft

router = APIRouter(prefix="/v1", tags=["events"])


class BatchIngestRequest(BaseModel):
    """Wire shape for POST /v1/events."""

    model_config = ConfigDict(extra="forbid")

    events: list[LogEventDraft] = Field(min_length=1, max_length=1000)


class RejectionInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    index: int
    reason: str


class BatchIngestResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    accepted: int
    rejected: list[RejectionInfo]


@router.post(
    "/events",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=BatchIngestResponse,
    responses={
        status.HTTP_400_BAD_REQUEST: {
            "model": ErrorResponse,
            "description": (
                "Malformed request body (FastAPI's default 400 path for "
                "syntactically-invalid JSON, wrong Content-Type, etc.)."
            ),
        },
        # 422 is auto-documented by FastAPI as `HTTPValidationError`
        # whose `detail` is a list of validation errors. We deliberately
        # do NOT override it with our `ErrorResponse(detail: str)` —
        # the auto-shape is the truth (schemathesis catches the mismatch
        # the moment we lie about it).
    },
)
async def ingest_events(
    payload: BatchIngestRequest,
    service: IngestionServiceDep,
) -> BatchIngestResponse:
    accepted, rejected = await service.ingest_batch(payload.events)
    return BatchIngestResponse(
        accepted=accepted,
        rejected=[RejectionInfo(index=i, reason=r) for i, r in rejected],
    )
