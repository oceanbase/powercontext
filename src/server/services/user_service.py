"""
User service for PowerMem API
"""

import logging
from typing import Any, Dict, List, Optional
from powermem import UserMemory, auto_config
from ..models.errors import ErrorCode, APIError

logger = logging.getLogger("server")


class UserService:
    """Service for user profile operations"""
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        Initialize user service.
        
        Args:
            config: PowerMem configuration (uses auto_config if None)
        """
        if config is None:
            config = auto_config()
        
        self.user_memory = UserMemory(config=config)
        logger.info("UserService initialized")
    
    def get_user_profile(self, user_id: str) -> Dict[str, Any]:
        """
        Get user profile.
        
        Args:
            user_id: User ID
            
        Returns:
            User profile data
            
        Raises:
            APIError: If profile not found or retrieval fails
        """
        try:
            if not user_id:
                raise APIError(
                    code=ErrorCode.INVALID_REQUEST,
                    message="user_id is required",
                    status_code=400,
                )
            
            profile = self.user_memory.profile(user_id)
            
            if not profile:
                raise APIError(
                    code=ErrorCode.USER_NOT_FOUND,
                    message=f"User profile for {user_id} not found",
                    status_code=404,
                )
            
            return profile
            
        except APIError:
            raise
        except Exception as e:
            logger.error(f"Failed to get user profile {user_id}: {e}", exc_info=True)
            raise APIError(
                code=ErrorCode.INTERNAL_ERROR,
                message=f"Failed to get user profile: {str(e)}",
                status_code=500,
            )
    
    def update_user_profile(
        self,
        user_id: str,
        profile_content: Optional[str] = None,
        topics: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Update user profile.
        
        Args:
            user_id: User ID
            profile_content: Profile content text
            topics: Structured topics dictionary
            
        Returns:
            Updated profile data
            
        Raises:
            APIError: If update fails
        """
        try:
            if not user_id:
                raise APIError(
                    code=ErrorCode.INVALID_REQUEST,
                    message="user_id is required",
                    status_code=400,
                )
            
            # Use UserMemory.add() to update profile by constructing a message
            # that contains the profile information we want to save
            # This follows the pattern shown in scenario_9_user_memory.md
            import json
            
            if topics is not None:
                # For structured topics, construct a message that will be extracted as topics
                message_content = json.dumps(topics, ensure_ascii=False)
                messages = [{"role": "user", "content": message_content}]
                result = self.user_memory.add(
                    messages=messages,
                    user_id=user_id,
                    profile_type="topics",
                )
            elif profile_content is not None:
                # For profile content, construct a message containing the profile
                messages = [{"role": "user", "content": profile_content}]
                result = self.user_memory.add(
                    messages=messages,
                    user_id=user_id,
                    profile_type="content",
                )
            else:
                # No profile data provided
                raise APIError(
                    code=ErrorCode.INVALID_REQUEST,
                    message="Either profile_content or topics must be provided",
                    status_code=400,
                )
            
            # Get updated profile using UserMemory.profile() interface
            profile = self.user_memory.profile(user_id)
            
            logger.info(f"User profile updated: {user_id}")
            return profile
            
        except APIError:
            raise
        except Exception as e:
            logger.error(f"Failed to update user profile {user_id}: {e}", exc_info=True)
            raise APIError(
                code=ErrorCode.PROFILE_UPDATE_FAILED,
                message=f"Failed to update user profile: {str(e)}",
                status_code=500,
            )
    
    def get_user_memories(
        self,
        user_id: str,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """
        Get all memories for a user.
        
        Args:
            user_id: User ID
            limit: Maximum number of results
            offset: Number of results to skip
            
        Returns:
            List of memories
        """
        try:
            if not user_id:
                raise APIError(
                    code=ErrorCode.INVALID_REQUEST,
                    message="user_id is required",
                    status_code=400,
                )
            
            result = self.user_memory.get_all(
                user_id=user_id,
                limit=limit,
                offset=offset,
            )
            
            # get_all returns a dict with "results" key, extract the list
            if isinstance(result, dict):
                memories = result.get("results", [])
            elif isinstance(result, list):
                memories = result
            else:
                memories = []
            
            return memories
            
        except APIError:
            raise
        except Exception as e:
            logger.error(f"Failed to get user memories {user_id}: {e}", exc_info=True)
            raise APIError(
                code=ErrorCode.INTERNAL_ERROR,
                message=f"Failed to get user memories: {str(e)}",
                status_code=500,
            )
    
    def delete_user_memories(self, user_id: str) -> Dict[str, Any]:
        """
        Delete all memories for a user (user profile deletion).
        
        Args:
            user_id: User ID
            
        Returns:
            Deletion result
            
        Raises:
            APIError: If deletion fails
        """
        try:
            if not user_id:
                raise APIError(
                    code=ErrorCode.INVALID_REQUEST,
                    message="user_id is required",
                    status_code=400,
                )
            
            # Get count of memories before deletion
            result = self.user_memory.get_all(user_id=user_id)
            if isinstance(result, dict):
                total = len(result.get("results", []))
            elif isinstance(result, list):
                total = len(result)
            else:
                total = 0
            
            # Use UserMemory.delete_all() to delete all memories for the user
            # This is the public interface method, not the internal memory.delete_all()
            success = self.user_memory.delete_all(user_id=user_id)
            
            # If delete_all was successful, all memories were deleted
            deleted_count = total if success else 0
            failed_count = 0 if success else total
            
            logger.info(f"Deleted {deleted_count} memories for user {user_id}")
            
            return {
                "user_id": user_id,
                "deleted_count": deleted_count,
                "failed_count": failed_count,
                "total": total,
            }
            
        except APIError:
            raise
        except Exception as e:
            logger.error(f"Failed to delete user memories {user_id}: {e}", exc_info=True)
            raise APIError(
                code=ErrorCode.INTERNAL_ERROR,
                message=f"Failed to delete user memories: {str(e)}",
                status_code=500,
            )
    
    def delete_user_profile(self, user_id: str) -> Dict[str, Any]:
        """
        Delete user profile.
        
        Args:
            user_id: User ID
            
        Returns:
            Deletion result
            
        Raises:
            APIError: If deletion fails
        """
        try:
            if not user_id:
                raise APIError(
                    code=ErrorCode.INVALID_REQUEST,
                    message="user_id is required",
                    status_code=400,
                )
            
            # Check if profile exists first
            profile = self.user_memory.profile(user_id)
            if not profile:
                raise APIError(
                    code=ErrorCode.USER_NOT_FOUND,
                    message=f"User profile for {user_id} not found",
                    status_code=404,
                )
            
            # Use UserMemory.delete_profile() to delete the profile
            # This is the public interface method
            success = self.user_memory.delete_profile(user_id=user_id)
            
            if not success:
                raise APIError(
                    code=ErrorCode.INTERNAL_ERROR,
                    message=f"Failed to delete user profile for {user_id}",
                    status_code=500,
                )
            
            logger.info(f"User profile deleted: {user_id}")
            
            return {
                "user_id": user_id,
                "deleted": True,
            }
            
        except APIError:
            raise
        except Exception as e:
            logger.error(f"Failed to delete user profile {user_id}: {e}", exc_info=True)
            raise APIError(
                code=ErrorCode.INTERNAL_ERROR,
                message=f"Failed to delete user profile: {str(e)}",
                status_code=500,
            )