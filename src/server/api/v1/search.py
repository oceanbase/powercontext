"""
Memory search API routes
"""

from datetime import datetime
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, Query, Request
from slowapi import Limiter
from slowapi.util import get_remote_address

from ...models.request import SearchRequest
from ...models.response import APIResponse, SearchResponse, SearchResult
from ...services.search_service import SearchService
from ...middleware.auth import verify_api_key
from ...middleware.rate_limit import limiter, get_rate_limit_string
from ...utils.converters import search_result_to_response
from .memories import parse_time_range_cutoff

router = APIRouter(prefix="/memories", tags=["search"])


_VALID_SORT_FIELDS = {"score", "created_at", "updated_at"}


def _coerce_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _apply_time_filter(items: List[Dict[str, Any]], cutoff: Optional[datetime]) -> List[Dict[str, Any]]:
    if cutoff is None:
        return items
    filtered = []
    for r in items:
        ts = _coerce_datetime(r.get("created_at"))
        if ts is not None and ts >= cutoff:
            filtered.append(r)
    return filtered


def _apply_sort(items: List[Dict[str, Any]], sort_by: Optional[str], order: str) -> List[Dict[str, Any]]:
    """Sort raw search results. Storage returns score-desc by default."""
    if sort_by is None or sort_by == "score":
        if order == "asc":
            return list(reversed(items))
        return items
    if sort_by not in _VALID_SORT_FIELDS:
        return items
    reverse = order != "asc"
    return sorted(
        items,
        key=lambda r: (_coerce_datetime(r.get(sort_by)) or datetime.min),
        reverse=reverse,
    )


def get_search_service(request: Request) -> SearchService:
    """Dependency to get search service singleton from app state"""
    service = request.app.state.search_service
    if service is None:
        from ...models.errors import ErrorCode, APIError
        raise APIError(
            code=ErrorCode.INTERNAL_ERROR,
            message="Search service unavailable: storage backend initialization failed",
            status_code=503,
        )
    return service


@router.post(
    "/search",
    response_model=APIResponse,
    summary="Search memories",
    description="Search memories using semantic search with optional filters",
)
@limiter.limit(get_rate_limit_string())
async def search_memories_post(
    request: Request,
    body: SearchRequest,
    api_key: str = Depends(verify_api_key),
    service: SearchService = Depends(get_search_service),
):
    """Search memories (POST method)"""
    cutoff = parse_time_range_cutoff(body.time_range)
    # When a time window is set, recall extra candidates so the post-filter
    # doesn't starve the response. Stay within storage limits.
    fetch_limit = min(body.limit * 5, 200) if cutoff is not None else body.limit

    results = service.search_memories(
        query=body.query,
        user_id=body.user_id,
        agent_id=body.agent_id,
        run_id=body.run_id,
        filters=body.filters,
        limit=fetch_limit,
    )

    raw_items = results.get("results", [])
    raw_items = _apply_time_filter(raw_items, cutoff)
    raw_items = _apply_sort(raw_items, body.sort_by, body.order)
    raw_items = raw_items[: body.limit]

    search_results = [search_result_to_response(r) for r in raw_items]

    response_data = SearchResponse(
        results=search_results,
        total=len(search_results),
        query=body.query,
    )

    return APIResponse(
        success=True,
        data=response_data.model_dump(mode='json'),
        message="Search completed successfully",
    )


@router.get(
    "/search",
    response_model=APIResponse,
    summary="Search memories (GET)",
    description="Search memories using query parameters",
)
@limiter.limit(get_rate_limit_string())
async def search_memories_get(
    request: Request,
    query: str = Query(..., description="Search query"),
    user_id: Optional[str] = Query(None, description="Filter by user ID"),
    agent_id: Optional[str] = Query(None, description="Filter by agent ID"),
    run_id: Optional[str] = Query(None, description="Filter by run ID"),
    limit: int = Query(30, ge=1, le=100, description="Maximum number of results"),
    api_key: str = Depends(verify_api_key),
    service: SearchService = Depends(get_search_service),
):
    """Search memories (GET method)"""
    results = service.search_memories(
        query=query,
        user_id=user_id,
        agent_id=agent_id,
        run_id=run_id,
        filters=None,  # GET method doesn't support complex filters
        limit=limit,
    )
    
    search_results = [
        search_result_to_response(r) for r in results.get("results", [])
    ]
    
    response_data = SearchResponse(
        results=search_results,
        total=len(search_results),
        query=query,
    )
    
    return APIResponse(
        success=True,
        data=response_data.model_dump(mode='json'),
        message="Search completed successfully",
    )
