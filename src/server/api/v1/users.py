"""
User profile API routes
"""

from typing import Optional
from fastapi import APIRouter, Depends, Query, Request
from slowapi import Limiter

from ...models.request import UserProfileUpdateRequest
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
    summary="Update user profile",
    description="Update the user profile for a specific user",
)
@limiter.limit(get_rate_limit_string())
async def update_user_profile(
    request: Request,
    user_id: str,
    body: UserProfileUpdateRequest,
    api_key: str = Depends(verify_api_key),
    service: UserService = Depends(get_user_service),
):
    """Update user profile"""
    profile = service.update_user_profile(
        user_id=user_id,
        profile_content=body.profile_content,
        topics=body.topics,
    )
    
    profile_response = user_profile_to_response(user_id, profile)
    
    return APIResponse(
        success=True,
        data=profile_response.model_dump(mode='json'),
        message="User profile updated successfully",
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
