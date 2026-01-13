"""
User profile API routes
"""

from typing import Optional
from fastapi import APIRouter, Depends, Query, Request
from slowapi import Limiter

from ...models.request import UserProfileAddRequest, UserProfileUpdateRequest
from ...models.response import APIResponse, UserProfileResponse, MemoryListResponse
from ...services.user_service import UserService
from ...middleware.auth import verify_api_key
from ...middleware.rate_limit import limiter, get_rate_limit_string
from ...utils.converters import user_profile_to_response, memory_dict_to_response

router = APIRouter(prefix="/users", tags=["users"])


def get_user_service() -> UserService:
    """Dependency to get user service"""
    return UserService()


@router.get(
    "/{user_id}/profile",
    response_model=APIResponse,
    summary="Get user profile",
    description="Get the user profile for a specific user",
)
@limiter.limit(get_rate_limit_string())
async def get_user_profile(
    request: Request,
    user_id: str,
    api_key: str = Depends(verify_api_key),
    service: UserService = Depends(get_user_service),
):
    """Get user profile"""
    profile = service.get_user_profile(user_id)
    
    profile_response = user_profile_to_response(user_id, profile)
    
    return APIResponse(
        success=True,
        data=profile_response.model_dump(mode='json'),
        message="User profile retrieved successfully",
    )


@router.post(
    "/{user_id}/profile",
    response_model=APIResponse,
    summary="Add messages and extract user profile",
    description="Add conversation messages and extract user profile information",
)
@limiter.limit(get_rate_limit_string())
async def add_user_profile(
    request: Request,
    user_id: str,
    body: UserProfileAddRequest,
    api_key: str = Depends(verify_api_key),
    service: UserService = Depends(get_user_service),
):
    """Add messages and extract user profile"""
    result = service.add_user_profile(
        user_id=user_id,
        messages=body.messages,
        agent_id=body.agent_id,
        run_id=body.run_id,
        metadata=body.metadata,
        filters=body.filters,
        scope=body.scope,
        memory_type=body.memory_type,
        prompt=body.prompt,
        infer=body.infer,
        profile_type=body.profile_type,
        custom_topics=body.custom_topics,
        strict_mode=body.strict_mode,
        include_roles=body.include_roles,
        exclude_roles=body.exclude_roles,
    )
    
    return APIResponse(
        success=True,
        data=result,
        message="Messages added and profile extracted successfully",
    )


@router.put(
    "/{user_id}/memories/{memory_id}",
    response_model=APIResponse,
    summary="Update user memory",
    description="Update an existing memory for a specific user",
)
@limiter.limit(get_rate_limit_string())
async def update_user_memory(
    request: Request,
    user_id: str,
    memory_id: int,
    body: UserProfileUpdateRequest,
    api_key: str = Depends(verify_api_key),
    service: UserService = Depends(get_user_service),
):
    """Update user memory"""
    result = service.update_user_memory(
        user_id=user_id,
        memory_id=memory_id,
        content=body.content,
        agent_id=body.agent_id,
        metadata=body.metadata,
    )
    
    memory_response = memory_dict_to_response(result)
    
    return APIResponse(
        success=True,
        data=memory_response.model_dump(mode='json'),
        message="Memory updated successfully",
    )


@router.get(
    "/{user_id}/memories",
    response_model=APIResponse,
    summary="Get user memories",
    description="Get all memories for a specific user",
)
@limiter.limit(get_rate_limit_string())
async def get_user_memories(
    request: Request,
    user_id: str,
    limit: int = Query(100, ge=1, le=1000, description="Maximum number of results"),
    offset: int = Query(0, ge=0, description="Number of results to skip"),
    api_key: str = Depends(verify_api_key),
    service: UserService = Depends(get_user_service),
):
    """Get all memories for a user"""
    memories = service.get_user_memories(
        user_id=user_id,
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
        message="User memories retrieved successfully",
    )


@router.delete(
    "/{user_id}/profile",
    response_model=APIResponse,
    summary="Delete user profile",
    description="Delete the user profile for a specific user",
)
@limiter.limit(get_rate_limit_string())
async def delete_user_profile(
    request: Request,
    user_id: str,
    api_key: str = Depends(verify_api_key),
    service: UserService = Depends(get_user_service),
):
    """Delete user profile"""
    result = service.delete_user_profile(user_id=user_id)
    
    return APIResponse(
        success=True,
        data=result,
        message=f"User profile for {user_id} deleted successfully",
    )


@router.delete(
    "/{user_id}/memories",
    response_model=APIResponse,
    summary="Delete user memories",
    description="Delete all memories for a specific user (user profile deletion)",
)
@limiter.limit(get_rate_limit_string())
async def delete_user_memories(
    request: Request,
    user_id: str,
    api_key: str = Depends(verify_api_key),
    service: UserService = Depends(get_user_service),
):
    """Delete all memories for a user"""
    result = service.delete_user_memories(user_id=user_id)
    
    return APIResponse(
        success=True,
        data=result,
        message=f"Deleted {result['deleted_count']} memories for user {user_id}",
    )
