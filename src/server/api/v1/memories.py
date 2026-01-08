"""
Memory management API routes
"""

import logging
from typing import List, Optional
from fastapi import APIRouter, Depends, Query, Request
from slowapi import Limiter
from slowapi.util import get_remote_address

from ...models.request import (
    MemoryCreateRequest,
    MemoryBatchCreateRequest,
    MemoryUpdateRequest,
    MemoryBatchUpdateRequest,
    BulkDeleteRequest,
)
from ...models.response import (
    APIResponse,
    MemoryResponse,
    MemoryListResponse,
)
from ...services.memory_service import MemoryService
from ...middleware.auth import verify_api_key
from ...middleware.rate_limit import limiter, get_rate_limit_string
from ...utils.converters import memory_dict_to_response

logger = logging.getLogger("server")

router = APIRouter(prefix="/memories", tags=["memories"])


def get_memory_service() -> MemoryService:
    """Dependency to get memory service"""
    return MemoryService()


@router.post(
    "",
    response_model=APIResponse,
    summary="Create a memory",
    description="Create a new memory with optional user_id, agent_id, and metadata",
)
@limiter.limit(get_rate_limit_string())
async def create_memory(
    request: Request,
    body: MemoryCreateRequest,
    api_key: str = Depends(verify_api_key),
    service: MemoryService = Depends(get_memory_service),
):
    """Create a new memory"""
    results = service.create_memory(
        content=body.content,
        user_id=body.user_id,
        agent_id=body.agent_id,
        run_id=body.run_id,
        metadata=body.metadata,
        filters=body.filters,
        scope=body.scope,
        memory_type=body.memory_type,
        infer=body.infer,
    )
    
    # Convert all created memories to response format
    # results is now a list of memory dictionaries
    memory_responses = [memory_dict_to_response(m) for m in results]
    
    # Always return array of memories
    # Exclude None values to avoid returning null fields
    message = "Memory created successfully" if len(memory_responses) == 1 else f"Created {len(memory_responses)} memories successfully"
    
    return APIResponse(
        success=True,
        data=[m.model_dump(mode='json', exclude_none=True) for m in memory_responses],
        message=message,
    )


@router.post(
    "/batch",
    response_model=APIResponse,
    summary="Create multiple memories",
    description="Create multiple memories in a single request (batch operation)",
)
@limiter.limit(get_rate_limit_string())
async def batch_create_memories(
    request: Request,
    body: MemoryBatchCreateRequest,
    api_key: str = Depends(verify_api_key),
    service: MemoryService = Depends(get_memory_service),
):
    """Create multiple memories in batch"""
    # Convert MemoryItem objects to dictionaries
    memories_data = [
        {
            "content": item.content,
            "metadata": item.metadata,
            "filters": item.filters,
            "scope": item.scope,
            "memory_type": item.memory_type,
        }
        for item in body.memories
    ]
    
    result = service.batch_create_memories(
        memories=memories_data,
        user_id=body.user_id,
        agent_id=body.agent_id,
        run_id=body.run_id,
        infer=body.infer,
    )
    
    # Convert created memories to response format
    created_memories = []
    for item in result["created"]:
        try:
            memory = service.get_memory(
                memory_id=item["memory_id"],
                user_id=body.user_id,
                agent_id=body.agent_id,
            )
            created_memories.append(memory_dict_to_response(memory).model_dump(mode='json'))
        except Exception as e:
            logger.warning(f"Failed to retrieve created memory {item['memory_id']}: {e}")
            # Include basic info even if full retrieval fails
            created_memories.append({
                "memory_id": item["memory_id"],
                "content": item["content"],
            })
    
    response_data = {
        "memories": created_memories,
        "total": result["total"],
        "created_count": result["created_count"],
        "failed_count": result["failed_count"],
    }
    
    # Only include failed items if there are any
    if result["failed_count"] > 0:
        response_data["failed"] = result["failed"]
    
    return APIResponse(
        success=True,
        data=response_data,
        message=f"Created {result['created_count']} out of {result['total']} memories",
    )


@router.get(
    "",
    response_model=APIResponse,
    summary="List memories",
    description="Get a list of memories with optional filtering and pagination",
)
@limiter.limit(get_rate_limit_string())
async def list_memories(
    request: Request,
    user_id: Optional[str] = Query(None, description="Filter by user ID"),
    agent_id: Optional[str] = Query(None, description="Filter by agent ID"),
    limit: int = Query(100, ge=1, le=1000, description="Maximum number of results"),
    offset: int = Query(0, ge=0, description="Number of results to skip"),
    api_key: str = Depends(verify_api_key),
    service: MemoryService = Depends(get_memory_service),
):
    """List memories with pagination"""
    memories = service.list_memories(
        user_id=user_id,
        agent_id=agent_id,
        limit=limit,
        offset=offset,
    )
    
    memory_responses = [memory_dict_to_response(m) for m in memories]
    
    response_data = MemoryListResponse(
        memories=memory_responses,
        total=len(memory_responses),
        limit=limit,
        offset=offset,
    )
    
    return APIResponse(
        success=True,
        data=response_data.model_dump(mode='json'),
        message="Memories retrieved successfully",
    )


@router.get(
    "/{memory_id}",
    response_model=APIResponse,
    summary="Get a memory",
    description="Get a specific memory by ID",
)
@limiter.limit(get_rate_limit_string())
async def get_memory(
    request: Request,
    memory_id: int,
    user_id: Optional[str] = Query(None, description="User ID for access control"),
    agent_id: Optional[str] = Query(None, description="Agent ID for access control"),
    api_key: str = Depends(verify_api_key),
    service: MemoryService = Depends(get_memory_service),
):
    """Get a memory by ID"""
    memory = service.get_memory(
        memory_id=memory_id,
        user_id=user_id,
        agent_id=agent_id,
    )
    
    memory_response = memory_dict_to_response(memory)
    
    return APIResponse(
        success=True,
        data=memory_response.model_dump(mode='json'),
        message="Memory retrieved successfully",
    )


@router.put(
    "/batch",
    response_model=APIResponse,
    summary="Batch update memories",
    description="Update multiple memories in a single request (batch operation)",
)
@limiter.limit(get_rate_limit_string())
async def batch_update_memories(
    request: Request,
    body: MemoryBatchUpdateRequest,
    api_key: str = Depends(verify_api_key),
    service: MemoryService = Depends(get_memory_service),
):
    """Update multiple memories in batch"""
    # Convert MemoryUpdateItem objects to dictionaries
    updates_data = [
        {
            "memory_id": item.memory_id,
            "content": item.content,
            "metadata": item.metadata,
        }
        for item in body.updates
    ]
    
    result = service.batch_update_memories(
        updates=updates_data,
        user_id=body.user_id,
        agent_id=body.agent_id,
    )
    
    # Convert updated memories to response format
    updated_memories = []
    for item in result["updated"]:
        try:
            memory = service.get_memory(
                memory_id=item["memory_id"],
                user_id=body.user_id,
                agent_id=body.agent_id,
            )
            updated_memories.append(memory_dict_to_response(memory).model_dump(mode='json'))
        except Exception as e:
            logger.warning(f"Failed to retrieve updated memory {item['memory_id']}: {e}")
            # Include basic info even if full retrieval fails
            updated_memories.append({
                "memory_id": item["memory_id"],
            })
    
    response_data = {
        "memories": updated_memories,
        "total": result["total"],
        "updated_count": result["updated_count"],
        "failed_count": result["failed_count"],
    }
    
    # Only include failed items if there are any
    if result["failed_count"] > 0:
        response_data["failed"] = result["failed"]
    
    return APIResponse(
        success=True,
        data=response_data,
        message=f"Updated {result['updated_count']} out of {result['total']} memories",
    )


@router.put(
    "/{memory_id}",
    response_model=APIResponse,
    summary="Update a memory",
    description="Update an existing memory",
)
@limiter.limit(get_rate_limit_string())
async def update_memory(
    request: Request,
    memory_id: int,
    body: MemoryUpdateRequest,
    user_id: Optional[str] = Query(None, description="User ID for access control"),
    agent_id: Optional[str] = Query(None, description="Agent ID for access control"),
    api_key: str = Depends(verify_api_key),
    service: MemoryService = Depends(get_memory_service),
):
    """Update a memory"""
    # At least one of content or metadata must be provided
    if body.content is None and body.metadata is None:
        from ...models.errors import ErrorCode, APIError
        raise APIError(
            code=ErrorCode.INVALID_REQUEST,
            message="At least one of content or metadata must be provided",
            status_code=400,
        )
    
    result = service.update_memory(
        memory_id=memory_id,
        content=body.content,
        user_id=user_id,
        agent_id=agent_id,
        metadata=body.metadata,
    )
    
    memory_response = memory_dict_to_response(result)
    
    return APIResponse(
        success=True,
        data=memory_response.model_dump(mode='json'),
        message="Memory updated successfully",
    )


@router.delete(
    "/batch",
    response_model=APIResponse,
    summary="Bulk delete memories",
    description="Delete multiple memories at once",
)
@limiter.limit(get_rate_limit_string())
async def bulk_delete_memories(
    request: Request,
    body: BulkDeleteRequest,
    api_key: str = Depends(verify_api_key),
    service: MemoryService = Depends(get_memory_service),
):
    """Bulk delete memories"""
    result = service.bulk_delete_memories(
        memory_ids=body.memory_ids,
        user_id=body.user_id,
        agent_id=body.agent_id,
    )
    
    return APIResponse(
        success=True,
        data=result,
        message=f"Deleted {result['deleted_count']} memories",
    )


@router.delete(
    "/{memory_id}",
    response_model=APIResponse,
    summary="Delete a memory",
    description="Delete a specific memory by ID",
)
@limiter.limit(get_rate_limit_string())
async def delete_memory(
    request: Request,
    memory_id: int,
    user_id: Optional[str] = Query(None, description="User ID for access control"),
    agent_id: Optional[str] = Query(None, description="Agent ID for access control"),
    api_key: str = Depends(verify_api_key),
    service: MemoryService = Depends(get_memory_service),
):
    """Delete a memory"""
    service.delete_memory(
        memory_id=memory_id,
        user_id=user_id,
        agent_id=agent_id,
    )
    
    return APIResponse(
        success=True,
        data={"memory_id": memory_id},
        message="Memory deleted successfully",
    )
