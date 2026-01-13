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
    
    def add_user_profile(
        self,
        user_id: str,
        messages: Any,
        agent_id: Optional[str] = None,
        run_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        filters: Optional[Dict[str, Any]] = None,
        scope: Optional[str] = None,
        memory_type: Optional[str] = None,
        prompt: Optional[str] = None,
        infer: bool = True,
        profile_type: str = "content",
        custom_topics: Optional[str] = None,
        strict_mode: bool = False,
        include_roles: Optional[List[str]] = ["user"],
        exclude_roles: Optional[List[str]] = ["assistant"],
    ) -> Dict[str, Any]:
        """
        Add messages and extract user profile.
        
        Args:
            user_id: User ID
            messages: Conversation messages (str, dict, or list[dict])
            agent_id: Agent identifier
            run_id: Run/session identifier
            metadata: Additional metadata
            filters: Filter metadata
            scope: Memory scope
            memory_type: Memory type classification
            prompt: Custom prompt for intelligent processing
            infer: Enable intelligent memory processing
            profile_type: Profile extraction type: 'content' or 'topics'
            custom_topics: Custom topics JSON string for structured extraction
            strict_mode: Only output topics from provided list
            include_roles: Roles to include when filtering messages
            exclude_roles: Roles to exclude when filtering messages
            
        Returns:
            Result dict with memory and profile extraction results
            
        Raises:
            APIError: If operation fails
        """
        try:
            if not user_id:
                raise APIError(
                    code=ErrorCode.INVALID_REQUEST,
                    message="user_id is required",
                    status_code=400,
                )
            
            result = self.user_memory.add(
                messages=messages,
                user_id=user_id,
                agent_id=agent_id,
                run_id=run_id,
                metadata=metadata,
                filters=filters,
                scope=scope,
                memory_type=memory_type,
                prompt=prompt,
                infer=infer,
                profile_type=profile_type,
                custom_topics=custom_topics,
                strict_mode=strict_mode,
                include_roles=include_roles,
                exclude_roles=exclude_roles,
            )
            
            logger.info(f"User profile added: {user_id}")
            return result
            
        except APIError:
            raise
        except Exception as e:
            logger.error(f"Failed to add user profile {user_id}: {e}", exc_info=True)
            raise APIError(
                code=ErrorCode.PROFILE_UPDATE_FAILED,
                message=f"Failed to add user profile: {str(e)}",
                status_code=500,
            )

    def update_user_memory(
        self,
        user_id: str,
        memory_id: int,
        content: str,
        agent_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Update an existing user memory.
        
        Args:
            user_id: User ID
            memory_id: Memory ID to update
            content: New content for the memory
            agent_id: Agent identifier for access control
            metadata: Updated metadata
            
        Returns:
            Updated memory data
            
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
            
            result = self.user_memory.update(
                memory_id=memory_id,
                content=content,
                user_id=user_id,
                agent_id=agent_id,
                metadata=metadata,
            )
            
            logger.info(f"User memory updated: user_id={user_id}, memory_id={memory_id}")
            return result
            
        except APIError:
            raise
        except Exception as e:
            logger.error(f"Failed to update user memory {memory_id}: {e}", exc_info=True)
            raise APIError(
                code=ErrorCode.INTERNAL_ERROR,
                message=f"Failed to update user memory: {str(e)}",
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