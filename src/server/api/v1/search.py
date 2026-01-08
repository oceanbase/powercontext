"""
Memory search API routes
"""

from typing import Optional
from fastapi import APIRouter, Depends, Query, Request
from slowapi import Limiter
from slowapi.util import get_remote_address

from ...models.request import SearchRequest
from ...models.response import APIResponse, SearchResponse, SearchResult
from ...services.search_service import SearchService
from ...middleware.auth import verify_api_key
from ...middleware.rate_limit import limiter, get_rate_limit_string
from ...utils.converters import search_result_to_response

router = APIRouter(prefix="/memories", tags=["search"])


def get_search_service() -> SearchService:
    """Dependency to get search service"""
    return SearchService()


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
    results = service.search_memories(
        query=body.query,
        user_id=body.user_id,
        agent_id=body.agent_id,
        run_id=body.run_id,
        filters=body.filters,
        limit=body.limit,
    )
    
    search_results = [
        search_result_to_response(r) for r in results.get("results", [])
    ]
    
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
