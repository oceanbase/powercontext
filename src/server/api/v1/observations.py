"""
Structured observation ingest API routes
"""

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Request

from ...middleware.auth import verify_api_key
from ...middleware.rate_limit import get_rate_limit_string, limiter
from ...models.request import (
    ObservationBatchIngestRequest,
    ObservationIngestRequest,
)
from ...models.response import (
    APIResponse,
    ObservationBatchIngestResponse,
    ObservationBatchItemResponse,
    ObservationIngestResponse,
)
from ...services.memory_service import MemoryService
from ...utils.converters import memory_dict_to_response
from .memories import get_memory_service

logger = logging.getLogger("server")

router = APIRouter(prefix="/observations", tags=["observations"])


def _memory_response(memory: Optional[Dict[str, Any]]):
    """Convert raw memory dictionaries to API memory response models."""
    if not memory:
        return None
    return memory_dict_to_response(memory)


def _observation_result_response(
    result: Dict[str, Any],
) -> ObservationIngestResponse:
    """Convert a service observation ingest result to a response model."""
    raw_memory = _memory_response(result.get("raw_memory"))
    semantic_memories = [
        memory_dict_to_response(memory)
        for memory in result.get("semantic_memories", [])
    ]
    memories = [
        memory_dict_to_response(memory)
        for memory in result.get("memories", [])
    ]
    return ObservationIngestResponse(
        observation_id=result.get("observation_id"),
        raw_memory=raw_memory,
        semantic_memories=semantic_memories,
        memories=memories,
        saved_raw=bool(result.get("saved_raw")),
        inferred=bool(result.get("inferred")),
        deduped=bool(result.get("deduped")),
    )


@router.post(
    "",
    response_model=APIResponse,
    summary="Ingest a coding-agent observation",
    description="Persist a structured coding-agent observation as raw memory and optionally infer semantic memories",
)
@limiter.limit(get_rate_limit_string())
async def ingest_observation(
    request: Request,
    body: ObservationIngestRequest,
    api_key: str = Depends(verify_api_key),
    service: MemoryService = Depends(get_memory_service),
):
    """Ingest one structured coding-agent observation."""
    result = service.ingest_observation(body)
    response_data = _observation_result_response(result)

    return APIResponse(
        success=True,
        data=response_data.model_dump(mode="json", exclude_none=True),
        message="Observation ingested successfully",
    )


@router.post(
    "/batch",
    response_model=APIResponse,
    summary="Ingest coding-agent observations in batch",
    description="Persist multiple structured coding-agent observations with per-item partial failures",
)
@limiter.limit(get_rate_limit_string())
async def batch_ingest_observations(
    request: Request,
    body: ObservationBatchIngestRequest,
    api_key: str = Depends(verify_api_key),
    service: MemoryService = Depends(get_memory_service),
):
    """Ingest multiple structured coding-agent observations."""
    result = service.batch_ingest_observations(body.observations)
    items = []
    for item in result["items"]:
        response_result = None
        if item.get("result") is not None:
            response_result = _observation_result_response(item["result"])
        items.append(
            ObservationBatchItemResponse(
                index=item["index"],
                success=item["success"],
                observation_id=item.get("observation_id"),
                result=response_result,
                error=item.get("error"),
            )
        )

    response_data = ObservationBatchIngestResponse(
        items=items,
        total=result["total"],
        success_count=result["success_count"],
        failed_count=result["failed_count"],
    )

    return APIResponse(
        success=True,
        data=response_data.model_dump(mode="json", exclude_none=True),
        message=(
            f"Ingested {result['success_count']} out of "
            f"{result['total']} observations"
        ),
    )
