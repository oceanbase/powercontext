"""
Validation utilities for PowerMem API
"""

from typing import Optional
from ..models.errors import ErrorCode, APIError


def validate_user_id(user_id: Optional[str]) -> Optional[str]:
    """
    Validate user ID format.
    
    Args:
        user_id: User ID to validate
        
    Returns:
        Validated user ID
        
    Raises:
        APIError: If user ID is invalid
    """
    if user_id is None:
        return None
    
    if not isinstance(user_id, str) or not user_id.strip():
        raise APIError(
            code=ErrorCode.INVALID_REQUEST,
            message="Invalid user_id format",
            status_code=400,
        )
    
    return user_id.strip()


def validate_agent_id(agent_id: Optional[str]) -> Optional[str]:
    """
    Validate agent ID format.
    
    Args:
        agent_id: Agent ID to validate
        
    Returns:
        Validated agent ID
        
    Raises:
        APIError: If agent ID is invalid
    """
    if agent_id is None:
        return None
    
    if not isinstance(agent_id, str) or not agent_id.strip():
        raise APIError(
            code=ErrorCode.INVALID_REQUEST,
            message="Invalid agent_id format",
            status_code=400,
        )
    
    return agent_id.strip()
