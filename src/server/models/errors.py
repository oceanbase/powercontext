"""
Error codes and exception classes for PowerMem API
"""

from enum import Enum
from typing import Any, Dict, Optional


class ErrorCode(str, Enum):
    """Error codes for API responses"""
    
    # General errors
    INTERNAL_ERROR = "INTERNAL_ERROR"
    INVALID_REQUEST = "INVALID_REQUEST"
    UNAUTHORIZED = "UNAUTHORIZED"
    FORBIDDEN = "FORBIDDEN"
    NOT_FOUND = "NOT_FOUND"
    RATE_LIMIT_EXCEEDED = "RATE_LIMIT_EXCEEDED"
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"
    
    # Memory errors
    MEMORY_NOT_FOUND = "MEMORY_NOT_FOUND"
    MEMORY_CREATE_FAILED = "MEMORY_CREATE_FAILED"
    MEMORY_UPDATE_FAILED = "MEMORY_UPDATE_FAILED"
    MEMORY_DELETE_FAILED = "MEMORY_DELETE_FAILED"
    MEMORY_SEARCH_FAILED = "MEMORY_SEARCH_FAILED"
    MEMORY_VALIDATION_ERROR = "MEMORY_VALIDATION_ERROR"
    MEMORY_DUPLICATE = "MEMORY_DUPLICATE"
    MEMORY_BATCH_LIMIT_EXCEEDED = "MEMORY_BATCH_LIMIT_EXCEEDED"
    
    # Search errors
    SEARCH_FAILED = "SEARCH_FAILED"
    INVALID_SEARCH_PARAMS = "INVALID_SEARCH_PARAMS"
    
    # User errors
    USER_NOT_FOUND = "USER_NOT_FOUND"
    USER_PROFILE_NOT_FOUND = "USER_PROFILE_NOT_FOUND"
    USER_PROFILE_UPDATE_FAILED = "USER_PROFILE_UPDATE_FAILED"
    PROFILE_UPDATE_FAILED = "PROFILE_UPDATE_FAILED"  # Keep for backward compatibility
    
    # Agent errors
    AGENT_NOT_FOUND = "AGENT_NOT_FOUND"
    AGENT_MEMORY_ACCESS_DENIED = "AGENT_MEMORY_ACCESS_DENIED"
    AGENT_MEMORY_SHARE_FAILED = "AGENT_MEMORY_SHARE_FAILED"
    
    # System errors
    SYSTEM_STORAGE_ERROR = "SYSTEM_STORAGE_ERROR"
    SYSTEM_LLM_ERROR = "SYSTEM_LLM_ERROR"
    SYSTEM_CONFIG_ERROR = "SYSTEM_CONFIG_ERROR"
    
    # Configuration errors (deprecated, use SYSTEM_*)
    CONFIG_ERROR = "CONFIG_ERROR"
    STORAGE_ERROR = "STORAGE_ERROR"


class APIError(Exception):
    """Base exception for API errors"""
    
    def __init__(
        self,
        code: ErrorCode,
        message: str,
        details: Optional[Dict[str, Any]] = None,
        status_code: int = 500,
    ):
        self.code = code
        self.message = message
        self.details = details or {}
        self.status_code = status_code
        super().__init__(self.message)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert error to dictionary"""
        return {
            "code": self.code.value,
            "message": self.message,
            "details": self.details,
        }
